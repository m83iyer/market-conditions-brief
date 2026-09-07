(function () {
  "use strict";

  // ── gauge configuration (labels, band edges, education copy) ──────────────
  // edges/domain drive the band-bar marker position (see markerPercent);
  // band bar segments themselves are always drawn equal-width for legibility.
  var GAUGES = [
    {
      id: "volatility", group: "market", name: "Volatility",
      question: "How jumpy is the market?",
      method: "How big the market expects daily swings to be over the next month.",
      bandLabels: ["Calm", "Normal", "Elevated", "Extreme"],
      edges: [25, 75, 90], domain: [0, 100],
    },
    {
      id: "trend", group: "market", name: "Trend",
      question: "Where is the index vs its 200-day average?",
      method: "Today's index level compared with its average over the past 200 trading days.",
      bandLabels: ["Well below", "Below", "Near", "Above", "Well above"],
      edges: [-10, -3, 3, 10], domain: [-15, 15],
    },
    {
      id: "breadth", group: "market", name: "Breadth",
      question: "How many stocks are above their own 200-day average?",
      method: "Whether the index's move is shared by most of its members or carried by a few.",
      bandLabels: ["Narrow", "Mixed", "Broad", "Very broad"],
      edges: [30, 50, 75], domain: [0, 100],
    },
    {
      id: "rates", group: "macro", name: "Rates",
      question: "How high are government bond yields vs the last 10 years?",
      method: "The return on government bonds — the benchmark every other investment is measured against.",
      bandLabels: ["Low", "Mid-range", "High", "Very high"],
      edges: [25, 75, 90], domain: [0, 100],
    },
    {
      id: "dollar", group: "macro",
      name: { us: "Dollar", in: "Dollar" },
      question: "How much has the dollar moved in a year?",
      method: {
        us: "How much the dollar has moved against a basket of major currencies over the past year.",
        in: "A stronger dollar means each rupee buys fewer dollars.",
      },
      cardLabel: { us: "Dollar vs major currencies", in: "Dollar vs rupee" },
      bandLabels: ["Much weaker", "Weaker", "Steady", "Stronger", "Much stronger"],
      edges: [-8, -3, 3, 8], domain: [-12, 12],
    },
    {
      id: "oil", group: "macro", name: "Oil",
      question: "Where does the oil price sit vs the last 10 years?",
      method: "The input cost behind fuel, transport, plastics and much of inflation.",
      cardLabel: { us: "Oil", in: "Oil — global input" },
      bandLabels: ["Low", "Mid-range", "High", "Very high"],
      edges: [25, 75, 90], domain: [0, 100],
    },
  ];
  var GAUGE_BY_ID = {};
  GAUGES.forEach(function (g) { GAUGE_BY_ID[g.id] = g; });

  var STATE_INTENSITY = {
    calm: 0, normal: 1, elevated: 2, extreme: 3,
    low: 0, mid_range: 1, high: 2, very_high: 3,
    narrow: 0, mixed: 1, broad: 2, very_broad: 3,
    near: 0, below: 1, above: 1, well_below: 2, well_above: 2,
    steady: 0, weaker: 1, stronger: 1, much_weaker: 2, much_stronger: 2,
  };

  var state = {
    market: "us",
    conditions: { us: null, in: null },
    history: null,
    meta: null,
    scrubIndex: null, // null = live
    scrubDates: [],
  };

  // ── formatting ──────────────────────────────────────────────────────────

  function fmtValue(value, unit) {
    if (value === null || value === undefined) return "—";
    if (unit === "index") return value.toFixed(2);
    if (unit === "pct_of_avg" || unit === "pct_change") {
      var sign = value > 0 ? "+" : "";
      return sign + value.toFixed(1) + "%";
    }
    if (unit === "pct_members" || unit === "pct_yield") return value.toFixed(1) + "%";
    if (unit === "usd_per_bbl") return "$" + value.toFixed(2);
    return String(value);
  }

  function labelFor(cfg, field, market) {
    var v = cfg[field];
    if (v === undefined) return "";
    if (typeof v === "string") return v;
    return v[market] || v.us || "";
  }

  function fmtAsOf(iso) {
    if (!iso) return "—";
    // handles both YYYY-MM-DD and YYYY-MM
    var parts = iso.split("-");
    var months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
    if (parts.length === 3) {
      var d = new Date(Date.UTC(+parts[0], +parts[1] - 1, +parts[2]));
      var wk = ["Sun","Mon","Tue","Wed","Thu","Fri","Sat"][d.getUTCDay()];
      return wk + " " + d.getUTCDate() + " " + months[d.getUTCMonth()] + " " + d.getUTCFullYear();
    }
    if (parts.length === 2) {
      return months[+parts[1] - 1] + " " + parts[0];
    }
    return iso;
  }

  // ── band-bar marker position (piecewise-linear within equal segments) ────

  function markerPercent(value, edges, domain) {
    var segs = edges.length + 1;
    var segWidth = 100 / segs;
    var segIdx = 0;
    for (var i = 0; i < edges.length; i++) { if (value >= edges[i]) segIdx = i + 1; }
    var lo = segIdx === 0 ? domain[0] : edges[segIdx - 1];
    var hi = segIdx === segs - 1 ? domain[1] : edges[segIdx];
    var frac = hi === lo ? 0.5 : (value - lo) / (hi - lo);
    frac = Math.max(0, Math.min(1, frac));
    return segIdx * segWidth + frac * segWidth;
  }

  // ── sparkline (inline SVG) ─────────────────────────────────────────────

  function sparklineSvg(points) {
    if (!points || points.length < 2) {
      return '<svg class="sparkline" viewBox="0 0 100 30" preserveAspectRatio="none"></svg>';
    }
    var vals = points.map(function (p) { return p[1]; });
    var min = Math.min.apply(null, vals), max = Math.max.apply(null, vals);
    var span = max - min || 1;
    var w = 100, h = 26, pad = 2;
    var d = points.map(function (p, i) {
      var x = (i / (points.length - 1)) * w;
      var y = pad + (1 - (p[1] - min) / span) * (h - pad * 2);
      return (i === 0 ? "M" : "L") + x.toFixed(2) + "," + y.toFixed(2);
    }).join(" ");
    return '<svg class="sparkline" viewBox="0 0 100 30" preserveAspectRatio="none"><path d="' + d + '"/></svg>';
  }

  // ── card rendering ─────────────────────────────────────────────────────

  function cardHtml(cfg, gauge, market) {
    var name = labelFor(cfg, "cardLabel", market) || labelFor(cfg, "name", market);
    var method = labelFor(cfg, "method", market);
    if (!gauge || gauge.status !== "READ") {
      var reasonText = gauge && gauge.no_read_reason
        ? { stale: "Data older than a week.", short_history: "Not enough history yet.",
            fetch_failed: "Source didn't answer this week.", coverage: "Fewer than 90% of members loaded.",
            integrity: "Numbers failed a sanity check." }[gauge.no_read_reason] || "No read this week."
        : "No read this week.";
      return (
        '<div class="gauge-card" data-gauge="' + cfg.id + '">' +
        '<p class="gauge-name">' + name + "</p>" +
        '<p class="gauge-question">' + cfg.question + "</p>" +
        '<div class="gauge-value-row"><span class="gauge-value">—</span></div>' +
        '<span class="state-pill no-read">No read</span>' +
        '<div class="band-bar hatched">' + cfg.bandLabels.map(function () { return '<div class="seg"></div>'; }).join("") + "</div>" +
        '<div class="band-labels">' + cfg.bandLabels.map(function (l) { return "<span>" + l + "</span>"; }).join("") + "</div>" +
        '<p class="no-read-reason">' + reasonText + "</p>" +
        "</div>"
      );
    }
    var intensity = STATE_INTENSITY[gauge.state] != null ? STATE_INTENSITY[gauge.state] : 1;
    var stateLabelIdx = cfg.bandLabels.length === 4
      ? intensity
      : ["near","below","above","well_below","well_above"].indexOf(gauge.state) >= 0
        ? { near: 2, below: 1, above: 3, well_below: 0, well_above: 4 }[gauge.state]
        : { steady: 2, weaker: 1, stronger: 3, much_weaker: 0, much_stronger: 4 }[gauge.state];
    var stateLabel = cfg.bandLabels[stateLabelIdx];
    var pct = markerPercent(gauge.position, cfg.edges, cfg.domain);
    return (
      '<div class="gauge-card" data-gauge="' + cfg.id + '">' +
      '<p class="gauge-name">' + name + "</p>" +
      '<p class="gauge-question">' + cfg.question + "</p>" +
      '<div class="gauge-value-row"><span class="gauge-value">' + fmtValue(gauge.value, gauge.value_unit) + "</span></div>" +
      '<span class="state-pill i' + intensity + '">' + stateLabel + "</span>" +
      '<div class="band-bar">' + cfg.bandLabels.map(function () { return '<div class="seg i' + intensity + '"></div>'; }).join("") + '<div class="marker" style="left:' + pct.toFixed(1) + '%"></div></div>' +
      '<div class="band-labels">' + cfg.bandLabels.map(function (l) { return "<span>" + l + "</span>"; }).join("") + "</div>" +
      sparklineSvg(gauge.spark) +
      '<p class="source-line">' + gauge.source.series + " · " + gauge.source.provider.replace(" via yfinance", "") + " · " + fmtAsOf(gauge.as_of) + "</p>" +
      "</div>"
    );
  }

  function render() {
    var market = state.market;
    var cond = state.conditions[market];
    var marketEl = document.getElementById("group-market");
    var macroEl = document.getElementById("group-macro");
    marketEl.innerHTML = "";
    macroEl.innerHTML = "";
    var asOfDates = [];
    GAUGES.forEach(function (cfg) {
      var g = cond && cond.gauges ? cond.gauges[cfg.id] : null;
      if (state.scrubIndex !== null && state.scrubIndex < state.scrubDates.length) {
        g = scrubGaugeValue(cfg, market, state.scrubDates[state.scrubIndex]);
      }
      if (g && g.as_of) asOfDates.push(g.as_of);
      var html = cardHtml(cfg, g, market);
      var wrap = document.createElement("div");
      wrap.innerHTML = html;
      var node = wrap.firstChild;
      node.addEventListener("click", function () { openExpand(cfg, market); });
      (cfg.group === "market" ? marketEl : macroEl).appendChild(node);
    });
    var asOfLine = document.getElementById("as-of-line");
    if (state.scrubIndex !== null) {
      asOfLine.textContent = "Replaying " + fmtAsOf(state.scrubDates[state.scrubIndex]);
    } else if (asOfDates.length) {
      asOfDates.sort();
      asOfLine.textContent = "As of " + fmtAsOf(asOfDates[asOfDates.length - 1]);
    } else {
      asOfLine.textContent = "Data not available yet";
    }
  }

  function scrubGaugeValue(cfg, market, dateStr) {
    var h = state.history && state.history[market] && state.history[market][cfg.id];
    if (!h || !h.length) {
      return { status: "NO_READ", no_read_reason: "short_history" };
    }
    var point = null;
    for (var i = 0; i < h.length; i++) { if (h[i][0] <= dateStr) point = h[i]; }
    if (!point) return { status: "NO_READ", no_read_reason: "short_history" };
    var unit = cfg.id === "trend" ? "pct_of_avg" : cfg.id === "dollar" ? "pct_change" : cfg.id === "rates" ? "pct_yield" : cfg.id === "oil" ? "usd_per_bbl" : "index";
    // history.json only ever carries volatility/trend/rates/dollar/oil
    // (breadth has no back-history by design, see fetch_conditions.py).
    // Percentile-kind points are [date, raw value, percentile] -- the raw
    // value is what the card displays, the PERCENTILE is what bands/
    // positions it (25/75/90 cut points), never the raw value itself.
    // Trend/dollar points are [date, value] -- value IS the position on
    // their fixed -15..15 / -12..12 scales, no separate percentile exists.
    var isPercentileKind = point.length >= 3;
    var value = point[1];
    var position = isPercentileKind ? point[2] : value;
    var stateLabel = cfg.id === "trend" ? bandTrend(value) : cfg.id === "dollar" ? bandDollar(value) : bandLadder(position);
    return {
      status: "READ", value: value, value_unit: unit, position: position, state: stateLabel,
      as_of: point[0], source: { series: cfg.id, provider: "Historical replay" },
      spark: h.filter(function (p) { return p[0] <= dateStr; }).slice(-12).map(function (p) { return [p[0], p[1]]; }),
    };
  }

  function bandLadder(v) {
    if (v < 25) return "calm_low";
    if (v < 75) return "normal_mid";
    if (v < 90) return "elevated_high";
    return "extreme_very";
  }
  function bandTrend(v) {
    if (v < -10) return "well_below";
    if (v < -3) return "below";
    if (v < 3) return "near";
    if (v < 10) return "above";
    return "well_above";
  }
  function bandDollar(v) {
    if (v < -8) return "much_weaker";
    if (v < -3) return "weaker";
    if (v < 3) return "steady";
    if (v < 8) return "stronger";
    return "much_stronger";
  }

  // scrub replay uses simplified state keys (calm_low/normal_mid/...) that
  // don't collide with the live per-gauge label sets above; map them back
  // to the same 4 canonical intensities via STATE_INTENSITY fallback.
  STATE_INTENSITY.calm_low = 0; STATE_INTENSITY.normal_mid = 1;
  STATE_INTENSITY.elevated_high = 2; STATE_INTENSITY.extreme_very = 3;

  // ── expand overlay + 10y chart ─────────────────────────────────────────

  function openExpand(cfg, market) {
    var overlay = document.getElementById("expand-overlay");
    document.getElementById("expand-title").textContent = labelFor(cfg, "cardLabel", market) || labelFor(cfg, "name", market);
    document.getElementById("expand-question").textContent = cfg.question;
    document.getElementById("expand-method").textContent = labelFor(cfg, "method", market);
    var cond = state.conditions[market];
    var g = cond && cond.gauges ? cond.gauges[cfg.id] : null;
    document.getElementById("expand-source").textContent = g && g.status === "READ"
      ? g.source.series + " · " + g.source.provider
      : "No current reading.";
    overlay.hidden = false;
    var h = state.history && state.history[market] && state.history[market][cfg.id];
    drawExpandChart(cfg, h || []);
  }

  function closeExpand() { document.getElementById("expand-overlay").hidden = true; }

  function drawExpandChart(cfg, points) {
    var canvas = document.getElementById("expand-chart");
    var wrap = document.getElementById("expand-chart-wrap");
    var dpr = window.devicePixelRatio || 1;
    var cssW = wrap.clientWidth, cssH = 220;
    canvas.width = cssW * dpr; canvas.height = cssH * dpr;
    canvas.style.width = cssW + "px"; canvas.style.height = cssH + "px";
    var ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cssW, cssH);
    if (!points.length) {
      ctx.fillStyle = "#6B7684";
      ctx.font = "13px Inter, sans-serif";
      ctx.fillText("No history available yet.", 12, cssH / 2);
      return;
    }
    var padL = 8, padR = 8, padT = 10, padB = 10;
    var domain = cfg.domain;
    var edges = cfg.edges;
    var bandBounds = [domain[0]].concat(edges).concat([domain[1]]);
    var plotH = cssH - padT - padB, plotW = cssW - padL - padR;

    function yFor(v) {
      var clamped = Math.max(domain[0], Math.min(domain[1], v));
      var frac = (clamped - domain[0]) / (domain[1] - domain[0]);
      return padT + (1 - frac) * plotH;
    }
    // band shading, single-hue intensity ramp (never red/green)
    var shadeColors = ["#F5F7F9", "#EAEEF2", "#DCE3EA", "#C7D1DB"];
    var segsCount = bandBounds.length - 1;
    for (var i = 0; i < segsCount; i++) {
      var y0 = yFor(bandBounds[i + 1]), y1 = yFor(bandBounds[i]);
      var shadeIdx = segsCount === 5 ? [3, 1, 0, 1, 3][i] : i;
      ctx.fillStyle = shadeColors[shadeIdx];
      ctx.fillRect(padL, y0, plotW, y1 - y0);
    }

    // Percentile-kind points are [date, raw value, percentile] -- the chart
    // Y-axis and band shading are on the PERCENTILE scale (domain [0,100]
    // for these gauges), so the line must plot p[2], not the raw p[1] (a
    // raw VIX/yield/oil-price plotted against a 0-100 domain would misalign
    // with its own band shading). Trend/dollar points are [date, value]
    // where the value already IS the position on their raw domain.
    function posOf(p) { return p.length >= 3 ? p[2] : p[1]; }
    var unit = cfg.id === "trend" ? "pct_of_avg" : cfg.id === "dollar" ? "pct_change"
      : cfg.id === "rates" ? "pct_yield" : cfg.id === "oil" ? "usd_per_bbl" : "index";

    ctx.beginPath();
    points.forEach(function (p, i) {
      var x = padL + (i / (points.length - 1)) * plotW;
      var y = yFor(posOf(p));
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.strokeStyle = "#16283D";
    ctx.lineWidth = 1.8;
    ctx.stroke();

    var tooltip = document.getElementById("expand-tooltip");
    canvas.onmousemove = function (e) {
      var rect = canvas.getBoundingClientRect();
      var x = e.clientX - rect.left;
      var idx = Math.round(((x - padL) / plotW) * (points.length - 1));
      idx = Math.max(0, Math.min(points.length - 1, idx));
      var p = points[idx];
      tooltip.style.display = "block";
      tooltip.style.left = Math.min(cssW - 100, Math.max(0, x - 40)) + "px";
      tooltip.style.top = (yFor(posOf(p)) - 40) + "px";
      tooltip.textContent = fmtAsOf(p[0]) + ": " + fmtValue(p[1], unit);
    };
    canvas.onmouseleave = function () { tooltip.style.display = "none"; };
  }

  // ── week scrubber ───────────────────────────────────────────────────────

  function buildScrubDates() {
    var h = state.history;
    if (!h) return [];
    var all = new Set();
    ["us", "in"].forEach(function (m) {
      if (!h[m]) return;
      Object.keys(h[m]).forEach(function (gid) {
        (h[m][gid] || []).forEach(function (p) { all.add(p[0]); });
      });
    });
    return Array.from(all).sort();
  }

  function wireScrubber() {
    var slider = document.getElementById("week-scrubber");
    var dates = state.scrubDates;
    slider.max = String(Math.max(0, dates.length - 1));
    slider.value = String(dates.length - 1);
    document.getElementById("scrub-min-label").textContent = dates.length ? fmtAsOf(dates[0]) : "";
    document.getElementById("scrub-max-label").textContent = dates.length ? fmtAsOf(dates[dates.length - 1]) : "";
    slider.addEventListener("input", function () {
      var idx = +slider.value;
      if (idx >= dates.length - 1) {
        state.scrubIndex = null;
        document.getElementById("scrub-date-label").textContent = "Live";
        document.getElementById("scrub-live-btn").disabled = true;
      } else {
        state.scrubIndex = idx;
        document.getElementById("scrub-date-label").textContent = fmtAsOf(dates[idx]);
        document.getElementById("scrub-live-btn").disabled = false;
      }
      render();
    });
    document.getElementById("scrub-live-btn").addEventListener("click", function () {
      slider.value = String(dates.length - 1);
      state.scrubIndex = null;
      document.getElementById("scrub-date-label").textContent = "Live";
      document.getElementById("scrub-live-btn").disabled = true;
      render();
    });
  }

  // ── boot ────────────────────────────────────────────────────────────────

  function loadJson(path) {
    return fetch(path, { cache: "no-store" }).then(function (r) {
      if (!r.ok) throw new Error("fetch failed: " + path);
      return r.json();
    });
  }

  function boot() {
    document.getElementById("market-toggle").addEventListener("click", function (e) {
      var btn = e.target.closest("button[data-market]");
      if (!btn) return;
      state.market = btn.getAttribute("data-market");
      Array.prototype.forEach.call(document.querySelectorAll("#market-toggle button"), function (b) {
        var active = b === btn;
        b.classList.toggle("active", active);
        b.setAttribute("aria-selected", active ? "true" : "false");
      });
      render();
    });
    document.getElementById("expand-close").addEventListener("click", closeExpand);
    document.getElementById("expand-overlay").addEventListener("click", function (e) {
      if (e.target.id === "expand-overlay") closeExpand();
    });

    Promise.all([
      loadJson("data/conditions_us.json").catch(function () { return null; }),
      loadJson("data/conditions_in.json").catch(function () { return null; }),
      loadJson("data/history.json").catch(function () { return null; }),
      loadJson("data/meta.json").catch(function () { return null; }),
    ]).then(function (results) {
      state.conditions.us = results[0];
      state.conditions.in = results[1];
      state.history = results[2];
      state.meta = results[3];
      state.scrubDates = buildScrubDates();
      wireScrubber();
      render();
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();

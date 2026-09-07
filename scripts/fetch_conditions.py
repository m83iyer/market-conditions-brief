"""Fetch live data and build docs/data/{conditions,history,breadth_ledger,
members_us,members_in,meta}.json for the Market Conditions Brief.

Six gauges, two markets (US, India), each gauge computed independently --
there is no composite score anywhere in this file. A gauge that cannot be
read honestly is written as NO_READ with a reason, never guessed at.

Whole-run failure policy: if fewer than 3 of 6 gauges READ for a market,
that market's conditions.json is NOT overwritten -- the last good file (and
its true, now-stale as-of dates) stays live, and meta.json records the
failed attempt.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
import sys
import time
import traceback
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gauges  # noqa: E402
import members  # noqa: E402

DOCS = Path(__file__).resolve().parents[1] / "docs"
DATA = DOCS / "data"

OECD_INDIA_URL = (
    "https://sdmx.oecd.org/public/rest/data/OECD.SDD.STES,DSD_STES@DF_FINMARK,4.0/"
    "IND.M.IRLT.PA.....?startPeriod=2011-01&dimensionAtObservation=AllDimensions"
)
OECD_HEADERS = {"Accept": "application/vnd.sdmx.data+csv;version=2.0"}

MIN_DAILY_REF = 2000
MIN_MONTHLY_REF = 96
MIN_READS_TO_PUBLISH = 3


def run_date_utc() -> dt.date:
    return dt.datetime.now(dt.timezone.utc).date()


# ── raw fetchers ─────────────────────────────────────────────────────────────

def fetch_yf_close(ticker: str, run_date: dt.date) -> tuple[pd.Series | None, pd.Series | None, str | None]:
    """Returns (completed_close_series, raw_close_series_for_integrity_check,
    error_message_or_None)."""
    try:
        h = yf.Ticker(ticker).history(period="max", auto_adjust=False, actions=False)
        if h.empty:
            return None, None, "empty_history"
        raw = h["Close"]
        completed = gauges.drop_in_progress(raw.dropna(), run_date)
        if completed.empty:
            return None, raw, "empty_after_dropna"
        return completed, raw, None
    except Exception as exc:  # noqa: BLE001
        return None, None, f"{type(exc).__name__}: {exc}"


def fetch_oecd_india_rates(timeout: int = 20) -> tuple[pd.Series | None, str | None]:
    try:
        resp = requests.get(OECD_INDIA_URL, headers=OECD_HEADERS, timeout=timeout)
        resp.raise_for_status()
        reader = csv.DictReader(io.StringIO(resp.text))
        points = {}
        for row in reader:
            period, value = row.get("TIME_PERIOD"), row.get("OBS_VALUE")
            if not period or value in (None, ""):
                continue
            try:
                points[pd.Period(period, "M").to_timestamp()] = float(value)
            except (ValueError, TypeError):
                continue
        if not points:
            return None, "empty_or_unparseable"
        return pd.Series(points).sort_index(), None
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"


def fetch_batch_closes(symbols: list[str], run_date: dt.date, period: str = "14mo") -> dict[str, pd.Series]:
    """Best-effort batch download; returns only symbols that actually came
    back with data. Breadth's own eligibility check handles short series."""
    out: dict[str, pd.Series] = {}
    if not symbols:
        return out
    chunk = 100
    for i in range(0, len(symbols), chunk):
        batch = symbols[i:i + chunk]
        try:
            df = yf.download(batch, period=period, auto_adjust=True, progress=False,
                              threads=True, group_by="column")
        except Exception:  # noqa: BLE001
            continue
        try:
            closes = df["Close"] if isinstance(df.columns, pd.MultiIndex) else df
        except (KeyError, TypeError):
            continue
        cols = closes.columns if hasattr(closes, "columns") else []
        for sym in cols:
            s = closes[sym].dropna()
            if s.empty:
                continue
            out[sym] = gauges.drop_in_progress(s, run_date)
        time.sleep(0.5)
    return out


# ── per-gauge builders ───────────────────────────────────────────────────────

def _spark(series: pd.Series, points: int = 12) -> list[list]:
    weekly = series.resample("W-FRI").last().dropna()
    tail = weekly.iloc[-points:]
    return [[d.strftime("%Y-%m-%d"), round(float(v), 4)] for d, v in tail.items()]


def _no_read(reason: str, as_of: dt.date | None, source: dict) -> dict:
    return {
        "status": "NO_READ", "no_read_reason": reason, "value": None, "value_unit": None,
        "position": None, "position_kind": None, "state": None,
        "as_of": as_of.strftime("%Y-%m-%d") if as_of else None,
        "window": None, "source": source, "spark": [],
    }


def build_percentile_gauge(series_or_none, error, source: dict, band_fn, value_unit: str,
                            run_date: dt.date, min_ref: int = MIN_DAILY_REF,
                            position_kind: str = "percentile_10y",
                            integrity_range: tuple[float, float] | None = None) -> dict:
    if series_or_none is None or series_or_none.empty:
        return _no_read("fetch_failed", None, source)
    value, pct, n_ref, as_of = gauges.percentile_rank(series_or_none, years=10)
    if gauges.is_stale_daily(as_of, run_date) if position_kind == "percentile_10y" else gauges.is_stale_monthly(as_of, run_date):
        return _no_read("stale", as_of, source)
    if integrity_range and value is not None and not (integrity_range[0] <= value <= integrity_range[1]):
        return _no_read("integrity", as_of, source)
    if gauges.has_short_history(n_ref, min_ref):
        return _no_read("short_history", as_of, source)
    return {
        "status": "READ", "no_read_reason": None, "value": round(value, 4), "value_unit": value_unit,
        "position": round(pct, 1), "position_kind": position_kind, "state": band_fn(pct),
        "as_of": as_of.strftime("%Y-%m-%d"),
        "window": {"years": 10, "n": n_ref},
        "source": source, "spark": _spark(series_or_none),
    }


def build_trend_gauge(series_or_none, source: dict, run_date: dt.date) -> dict:
    if series_or_none is None or series_or_none.empty:
        return _no_read("fetch_failed", None, source)
    close, sma, dist, as_of = gauges.sma_distance(series_or_none, n=200)
    if gauges.is_stale_daily(as_of, run_date):
        return _no_read("stale", as_of, source)
    if close is None:
        return _no_read("short_history", as_of, source)
    if gauges.max_gap_days(series_or_none, tail_n=200) > 10:
        return _no_read("integrity", as_of, source)
    return {
        "status": "READ", "no_read_reason": None, "value": round(dist, 2), "value_unit": "pct_of_avg",
        "position": round(dist, 2), "position_kind": "raw_pct_15", "state": gauges.band_trend(dist),
        "as_of": as_of.strftime("%Y-%m-%d"),
        "window": {"kind": "sma_sessions", "n": 200},
        "source": source, "spark": _spark(series_or_none),
    }


def build_dollar_gauge(series_or_none, source: dict, run_date: dt.date) -> dict:
    if series_or_none is None or series_or_none.empty:
        return _no_read("fetch_failed", None, source)
    now, base, chg, as_of = gauges.change_since(series_or_none, years=1.0)
    if gauges.is_stale_daily(as_of, run_date):
        return _no_read("stale", as_of, source)
    if chg is None:
        return _no_read("short_history", as_of, source)
    return {
        "status": "READ", "no_read_reason": None, "value": round(chg, 2), "value_unit": "pct_change",
        "position": round(chg, 2), "position_kind": "raw_pct_12", "state": gauges.band_dollar(chg),
        "as_of": as_of.strftime("%Y-%m-%d"),
        "window": {"kind": "yoy", "years": 1},
        "source": source, "spark": _spark(series_or_none),
    }


def build_breadth_gauge(prices: dict, n_listed: int, source: dict, run_date: dt.date) -> dict:
    if not prices:
        return _no_read("fetch_failed", None, source)
    pct, n_eligible, n_total = gauges.breadth_pct(prices, n=200)
    if pct is None:
        return _no_read("short_history", None, source)
    coverage = n_eligible / n_listed if n_listed else 0
    reference_date = max(s.index[-1] for s in prices.values() if len(s))
    as_of = reference_date.date()
    if gauges.is_stale_daily(as_of, run_date):
        return _no_read("stale", as_of, source)
    if coverage < 0.90:
        return _no_read("coverage", as_of, source)
    return {
        "status": "READ", "no_read_reason": None, "value": round(pct, 1), "value_unit": "pct_members",
        "position": round(pct, 1), "position_kind": "raw_pct_100", "state": gauges.band_breadth(pct),
        "as_of": as_of.strftime("%Y-%m-%d"),
        "window": {"n_eligible": n_eligible, "n_listed": n_listed},
        "source": source, "spark": [],
    }


# ── market assembly ──────────────────────────────────────────────────────────

def build_us(run_date: dt.date, sp500_syms: list[str]) -> dict:
    vix, _, verr = fetch_yf_close("^VIX", run_date)
    gspc, _, _ = fetch_yf_close("^GSPC", run_date)
    tnx, _, _ = fetch_yf_close("^TNX", run_date)
    dxy, _, _ = fetch_yf_close("DX-Y.NYB", run_date)
    brent, _, _ = fetch_yf_close("BZ=F", run_date)
    breadth_prices = fetch_batch_closes(sp500_syms, run_date)

    return {
        "volatility": build_percentile_gauge(
            vix, verr, {"series": "^VIX", "provider": "Yahoo Finance via yfinance"},
            gauges.band_volatility, "index", run_date, integrity_range=(0, 200)),
        "trend": build_trend_gauge(
            gspc, {"series": "^GSPC", "provider": "Yahoo Finance via yfinance"}, run_date),
        "breadth": build_breadth_gauge(
            breadth_prices, len(sp500_syms),
            {"series": "S&P 500 members", "provider": "Yahoo Finance via yfinance"}, run_date),
        "rates": build_percentile_gauge(
            tnx, None, {"series": "^TNX", "provider": "Yahoo Finance via yfinance"},
            gauges.band_rates, "pct_yield", run_date, integrity_range=(0, 30)),
        "dollar": build_dollar_gauge(
            dxy, {"series": "DX-Y.NYB", "provider": "Yahoo Finance via yfinance"}, run_date),
        "oil": build_percentile_gauge(
            brent, None, {"series": "BZ=F", "provider": "Yahoo Finance via yfinance (Brent)"},
            gauges.band_oil, "usd_per_bbl", run_date, integrity_range=(0, 1000)),
    }


def build_india(run_date: dt.date, nifty_syms: list[str]) -> dict:
    vix, _, _ = fetch_yf_close("^INDIAVIX", run_date)
    nsei, _, _ = fetch_yf_close("^NSEI", run_date)
    usdinr, _, _ = fetch_yf_close("USDINR=X", run_date)
    brent, _, _ = fetch_yf_close("BZ=F", run_date)
    india_rates, rerr = fetch_oecd_india_rates()
    breadth_prices = fetch_batch_closes(nifty_syms, run_date)

    rates_gauge = _no_read("fetch_failed", None, {"series": "IND.M.IRLT.PA", "provider": "OECD SDMX"})
    if india_rates is not None and not india_rates.empty:
        value, pct, n_ref, as_of = gauges.percentile_rank(india_rates, years=10)
        period_end = as_of
        if gauges.is_stale_monthly(period_end, run_date):
            rates_gauge = _no_read("stale", period_end, {"series": "IND.M.IRLT.PA", "provider": "OECD SDMX"})
        elif gauges.has_short_history(n_ref, MIN_MONTHLY_REF):
            rates_gauge = _no_read("short_history", period_end, {"series": "IND.M.IRLT.PA", "provider": "OECD SDMX"})
        elif not (0 <= value <= 30):
            rates_gauge = _no_read("integrity", period_end, {"series": "IND.M.IRLT.PA", "provider": "OECD SDMX"})
        else:
            rates_gauge = {
                "status": "READ", "no_read_reason": None, "value": round(value, 4), "value_unit": "pct_yield",
                "position": round(pct, 1), "position_kind": "percentile_120m", "state": gauges.band_rates(pct),
                "as_of": period_end.strftime("%Y-%m"),
                "window": {"years": 10, "n": n_ref},
                "source": {"series": "IND.M.IRLT.PA", "provider": "OECD SDMX"},
                "spark": _spark(india_rates),
            }

    return {
        "volatility": build_percentile_gauge(
            vix, None, {"series": "^INDIAVIX", "provider": "Yahoo Finance via yfinance"},
            gauges.band_volatility, "index", run_date, integrity_range=(0, 200)),
        "trend": build_trend_gauge(
            nsei, {"series": "^NSEI", "provider": "Yahoo Finance via yfinance"}, run_date),
        "breadth": build_breadth_gauge(
            breadth_prices, len(nifty_syms),
            {"series": "Nifty 500 members", "provider": "Yahoo Finance via yfinance"}, run_date),
        "rates": rates_gauge,
        "dollar": build_dollar_gauge(
            usdinr, {"series": "USDINR=X", "provider": "Yahoo Finance via yfinance"}, run_date),
        "oil": build_percentile_gauge(
            brent, None, {"series": "BZ=F", "provider": "Yahoo Finance via yfinance (Brent, global input)"},
            gauges.band_oil, "usd_per_bbl", run_date, integrity_range=(0, 1000)),
    }


# ── history (10y weekly, 5 gauges -- breadth is ledger-only) ────────────────

def build_history_series(kind: str, series: pd.Series, years: int = 10):
    """Weekly (Friday) samples of the same stat this gauge uses live, each
    computed against ONLY the data available as of that week -- not a
    lookback-biased single percentile applied retroactively."""
    if series is None or series.empty:
        return []
    weekly_dates = series.resample("W-FRI").last().dropna().index
    cutoff = weekly_dates[-1] - pd.DateOffset(years=years) if len(weekly_dates) else None
    weekly_dates = [d for d in weekly_dates if cutoff is None or d > cutoff]
    out = []
    for d in weekly_dates:
        as_of_series = series[series.index <= d]
        if as_of_series.empty:
            continue
        if kind == "percentile":
            value, pct, n_ref, as_of = gauges.percentile_rank(as_of_series, years=10)
            if pct is None:
                continue
            # [date, raw value, percentile] -- both are needed: the raw
            # value is what the card displays, the percentile is what
            # positions the band-bar marker and picks the state label.
            out.append([as_of.strftime("%Y-%m-%d"), round(value, 4), round(pct, 1)])
        elif kind == "trend":
            _, _, dist, as_of = gauges.sma_distance(as_of_series, n=200)
            if dist is None:
                continue
            out.append([as_of.strftime("%Y-%m-%d"), round(dist, 2)])
        elif kind == "dollar":
            _, _, chg, as_of = gauges.change_since(as_of_series, years=1.0)
            if chg is None:
                continue
            out.append([as_of.strftime("%Y-%m-%d"), round(chg, 2)])
    return out


# ── main ─────────────────────────────────────────────────────────────────────

def main() -> int:
    run_date = run_date_utc()
    DATA.mkdir(parents=True, exist_ok=True)
    attempt_log = {"run_date": run_date.strftime("%Y-%m-%d"), "gauges": {}}

    sp500_syms, sp500_meta = members.fetch_and_guard(
        members.fetch_sp500_symbols, DATA / "members_us.json", run_date.strftime("%Y-%m-%d"), "sp500")
    nifty_syms, nifty_meta = members.fetch_and_guard(
        members.fetch_nifty500_symbols, DATA / "members_in.json", run_date.strftime("%Y-%m-%d"), "nifty500")
    attempt_log["members"] = {"us": sp500_meta, "in": nifty_meta}

    for market, builder, syms in (("us", build_us, sp500_syms), ("in", build_india, nifty_syms)):
        try:
            gauge_dict = builder(run_date, syms)
        except Exception:  # noqa: BLE001
            attempt_log["gauges"][market] = {"status": "crashed", "trace": traceback.format_exc()[-2000:]}
            continue
        n_read = sum(1 for g in gauge_dict.values() if g["status"] == "READ")
        attempt_log["gauges"][market] = {
            "n_read": n_read,
            "reasons": {k: v.get("no_read_reason") for k, v in gauge_dict.items() if v["status"] != "READ"},
        }
        out_path = DATA / f"conditions_{market}.json"
        if n_read < MIN_READS_TO_PUBLISH:
            attempt_log["gauges"][market]["published"] = False
            continue
        with out_path.open("w", encoding="utf-8") as fh:
            json.dump({"market": market, "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                       "gauges": gauge_dict}, fh, indent=2, sort_keys=True)
            fh.write("\n")
        attempt_log["gauges"][market]["published"] = True

    # History: recompute for whichever series fetched cleanly this run
    # (cheap relative to the live fetch; re-derives from the same pulls above
    # rather than a second network round-trip).
    history = {}
    try:
        vix_us, _, _ = fetch_yf_close("^VIX", run_date)
        gspc, _, _ = fetch_yf_close("^GSPC", run_date)
        tnx, _, _ = fetch_yf_close("^TNX", run_date)
        dxy, _, _ = fetch_yf_close("DX-Y.NYB", run_date)
        brent, _, _ = fetch_yf_close("BZ=F", run_date)
        vix_in, _, _ = fetch_yf_close("^INDIAVIX", run_date)
        nsei, _, _ = fetch_yf_close("^NSEI", run_date)
        usdinr, _, _ = fetch_yf_close("USDINR=X", run_date)
        history = {
            "us": {
                "volatility": build_history_series("percentile", vix_us),
                "trend": build_history_series("trend", gspc),
                "rates": build_history_series("percentile", tnx),
                "dollar": build_history_series("dollar", dxy),
                "oil": build_history_series("percentile", brent),
            },
            "in": {
                "volatility": build_history_series("percentile", vix_in),
                "trend": build_history_series("trend", nsei),
                "dollar": build_history_series("dollar", usdinr),
                "oil": build_history_series("percentile", brent),
            },
        }
    except Exception:  # noqa: BLE001
        attempt_log["history_error"] = traceback.format_exc()[-2000:]
    if history:
        with (DATA / "history.json").open("w", encoding="utf-8") as fh:
            json.dump(history, fh)
            fh.write("\n")

    # Breadth ledger: one append-only row per market per run.
    ledger_path = DATA / "breadth_ledger.json"
    try:
        with ledger_path.open(encoding="utf-8") as fh:
            ledger = json.load(fh)
    except (OSError, json.JSONDecodeError):
        ledger = []
    for market in ("us", "in"):
        g = attempt_log["gauges"].get(market, {})
        cond_path = DATA / f"conditions_{market}.json"
        if g.get("published") and cond_path.exists():
            with cond_path.open(encoding="utf-8") as fh:
                cond = json.load(fh)
            b = cond["gauges"]["breadth"]
            if b["status"] == "READ":
                ledger.append([b["as_of"], market, b["value"],
                                b["window"]["n_eligible"], b["window"]["n_listed"]])
    with ledger_path.open("w", encoding="utf-8") as fh:
        json.dump(ledger, fh)
        fh.write("\n")

    with (DATA / "meta.json").open("w", encoding="utf-8") as fh:
        json.dump(attempt_log, fh, indent=2, sort_keys=True)
        fh.write("\n")

    print(json.dumps(attempt_log, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())

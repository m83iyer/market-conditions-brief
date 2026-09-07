"""Bright-line lint (locked spec, section 3): this product must never ship
advisory, valence, forecast, crowd/contrarian, or composite-score language.

Scope, deliberately narrow: only USER-VISIBLE COPY is scanned -- text nodes
in docs/index.html, quoted string literals in docs/app.js, and README.md.
Bare JS identifiers/property access (gauge.position), CSS properties
(position: relative), and JSON schema field names are NOT scanned -- "position"
is both a banned advisory verb ("position your portfolio") AND a legitimate
technical term here (a JSON field name, a CSS property), so the lint must
tell prose from code. This is why app.js accesses that field as `.position`
dot-notation everywhere, never `["position"]` bracket/quoted form -- keeping
it out of the string-literal extraction on purpose.

docs/data/*.json is scanned too, but skipped (not failed) if absent -- CI
runs on every push, including before fetch_conditions.py has ever produced
real data.
"""
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DOCS = REPO / "docs"

# Multi-word phrases first (checked as literal phrases), then single words
# (checked with \b...\b word boundaries, case-insensitive). Source: locked
# spec section 3.
BANNED_PHRASES = [
    "index of indices", "the crowd", "fear and greed", "fear & greed",
    "historically when", "points to", "due for",
]
BANNED_WORDS = [
    # aggregate / composite
    "composite", "overall", "score",
    # advisory verbs
    "buy", "sell", "hold", "trim", "avoid", "hedge", "rotate", "accumulate",
    "exit", "enter",
    # valence labels
    "bullish", "bearish", "positive", "negative", "healthy", "unhealthy",
    "overbought", "oversold", "cheap", "expensive", "attractive",
    "dangerous", "opportunity",
    # forecast language
    "likely", "expect", "signal", "signals", "suggests", "warning", "alert",
    "imminent", "overdue",
    # crowd / contrarian framing
    "everyone", "complacent", "euphoric", "panic", "capitulation", "greed",
    "contrarian",
]
# risk-on/off and headwind/tailwind use a hyphen/word combo -- match as phrases
BANNED_PHRASES += ["risk-on", "risk-off", "headwind", "tailwind"]

ARROW_CHARS = "↑↓▲▼⬆⬇⇧⇩"

_N_OF_6 = re.compile(r"\b\d\s*of\s*6\b", re.IGNORECASE)
_MARKET_SUBJECT = re.compile(r"\bthe market('s| is)\b", re.IGNORECASE)


def _word_pattern(word: str) -> re.Pattern:
    return re.compile(rf"\b{re.escape(word)}\b", re.IGNORECASE)


def _extract_html_text(html: str) -> str:
    html = re.sub(r"<script\b.*?</script>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<style\b.*?</style>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", html)
    for entity, ch in (("&amp;", "&"), ("&nbsp;", " "), ("&#39;", "'"), ("&quot;", '"')):
        text = text.replace(entity, ch)
    return text


_STRING_LITERAL_RE = re.compile(r'"([^"\\]*(?:\\.[^"\\]*)*)"|\'([^\'\\]*(?:\\.[^\'\\]*)*)\'')


def _extract_js_strings(js: str) -> str:
    js = re.sub(r"/\*.*?\*/", " ", js, flags=re.DOTALL)
    js = re.sub(r"(^|[^:])//.*$", r"\1", js, flags=re.MULTILINE)
    out = []
    for m in _STRING_LITERAL_RE.finditer(js):
        out.append(m.group(1) or m.group(2) or "")
    return "\n".join(out)


def _violations(text: str, source_label: str) -> list[str]:
    hits = []
    for phrase in BANNED_PHRASES:
        if phrase.lower() in text.lower():
            hits.append(f"{source_label}: banned phrase {phrase!r}")
    for word in BANNED_WORDS:
        if _word_pattern(word).search(text):
            hits.append(f"{source_label}: banned word {word!r}")
    if _N_OF_6.search(text):
        hits.append(f"{source_label}: banned 'N of 6' count pattern")
    if _MARKET_SUBJECT.search(text):
        hits.append(f"{source_label}: banned subject \"the market is/'s\"")
    for ch in ARROW_CHARS:
        if ch in text:
            hits.append(f"{source_label}: banned arrow character {ch!r}")
    return hits


def test_index_html_clean():
    path = DOCS / "index.html"
    text = _extract_html_text(path.read_text(encoding="utf-8"))
    violations = _violations(text, "docs/index.html")
    assert not violations, "\n".join(violations)


def test_app_js_clean():
    path = DOCS / "app.js"
    strings = _extract_js_strings(path.read_text(encoding="utf-8"))
    violations = _violations(strings, "docs/app.js")
    assert not violations, "\n".join(violations)


def test_readme_clean():
    path = REPO / "README.md"
    text = path.read_text(encoding="utf-8")
    violations = _violations(text, "README.md")
    assert not violations, "\n".join(violations)


def test_data_json_clean_if_present():
    data_dir = DOCS / "data"
    if not data_dir.exists():
        return
    violations = []
    for jf in sorted(data_dir.glob("*.json")):
        try:
            payload = json.loads(jf.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        flat = json.dumps(payload)
        violations.extend(_violations(flat, jf.name))
    assert not violations, "\n".join(violations)


def test_no_overall_or_composite_field_in_schema():
    """Structural guard, independent of wording: conditions_*.json must have
    exactly the six named gauges and nothing that looks like a combined
    verdict field."""
    for market in ("us", "in"):
        path = DOCS / "data" / f"conditions_{market}.json"
        if not path.exists():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        gauges = payload.get("gauges", {})
        assert set(gauges.keys()) == {
            "volatility", "trend", "breadth", "rates", "dollar", "oil",
        }, f"conditions_{market}.json has unexpected top-level gauge keys: {sorted(gauges.keys())}"
        for banned_key in ("overall", "composite", "score", "verdict", "summary_state"):
            assert banned_key not in payload, f"conditions_{market}.json has a banned top-level key: {banned_key!r}"

"""Index-member list fetchers for the Breadth gauge, with a drift guard.

Each run re-fetches the constituent list live. If the fetch fails, or the
new list differs from the last COMMITTED list by more than 15% (a broken
parse looks exactly like "half the index vanished"), the committed list is
kept instead -- see fetch_and_guard(). This is the only place either list is
allowed to change; fetch_conditions.py never edits members_*.json directly.
"""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import pandas as pd
import requests

WIKI_SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
NSE_NIFTY500_URL = "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv"

# NSE blocks the default python-requests UA; a plain browser UA is enough.
NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

DRIFT_GUARD_FRACTION = 0.15


def fetch_sp500_symbols(timeout: int = 20) -> list[str]:
    """Yahoo-ready symbols (dots replaced with dashes, e.g. BRK.B -> BRK-B).

    Uses pandas.read_html against the page's own `id="constituents"` table --
    a hand-rolled regex over Wikipedia's markup broke on the very first live
    run (0 symbols parsed, silently failing US breadth) because the actual
    cell markup didn't match the assumed `<td><a ...>` shape. read_html
    parses the real HTML table structure instead of guessing at it.
    """
    resp = requests.get(WIKI_SP500_URL, timeout=timeout, headers=NSE_HEADERS)
    resp.raise_for_status()
    tables = pd.read_html(io.StringIO(resp.text), attrs={"id": "constituents"})
    if not tables:
        return []
    symbols = tables[0]["Symbol"].astype(str).str.strip()
    seen, out = set(), []
    for s in symbols:
        y = s.replace(".", "-")
        if y and y not in seen:
            seen.add(y)
            out.append(y)
    return out


def fetch_nifty500_symbols(timeout: int = 20) -> list[str]:
    """Yahoo-ready symbols (NSE ticker + '.NS')."""
    resp = requests.get(NSE_NIFTY500_URL, timeout=timeout, headers=NSE_HEADERS)
    resp.raise_for_status()
    reader = csv.DictReader(io.StringIO(resp.content.decode("utf-8-sig")))
    out = []
    seen = set()
    for row in reader:
        sym = (row.get("Symbol") or "").strip()
        if not sym:
            continue
        y = sym + ".NS"
        if y not in seen:
            seen.add(y)
            out.append(y)
    return out


def _load_committed(path: Path) -> dict | None:
    try:
        with path.open(encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and isinstance(data.get("symbols"), list) and data["symbols"]:
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return None


def fetch_and_guard(fetch_fn, path: Path, list_as_of: str, label: str) -> tuple[list[str], dict]:
    """Fetch a fresh list; keep the committed one instead if the fetch fails
    or the new list drifts more than DRIFT_GUARD_FRACTION from it. Returns
    (symbols_to_use, meta) where meta records what actually happened."""
    committed = _load_committed(path)
    committed_symbols = committed["symbols"] if committed else []

    try:
        fresh = fetch_fn()
    except Exception as exc:  # noqa: BLE001 -- any network/parse failure
        if committed_symbols:
            return committed_symbols, {
                "source": "committed_fallback", "reason": f"fetch_error: {type(exc).__name__}",
                "label": label, "n": len(committed_symbols),
            }
        return [], {"source": "fetch_failed", "reason": type(exc).__name__, "label": label, "n": 0}

    if not fresh:
        if committed_symbols:
            return committed_symbols, {
                "source": "committed_fallback", "reason": "empty_parse",
                "label": label, "n": len(committed_symbols),
            }
        return [], {"source": "fetch_failed", "reason": "empty_parse", "label": label, "n": 0}

    if committed_symbols:
        old_set, new_set = set(committed_symbols), set(fresh)
        drift = len(old_set.symmetric_difference(new_set)) / max(len(old_set), 1)
        if drift > DRIFT_GUARD_FRACTION:
            return committed_symbols, {
                "source": "committed_fallback",
                "reason": f"drift_guard: {drift:.1%} > {DRIFT_GUARD_FRACTION:.0%}",
                "label": label, "n": len(committed_symbols), "fresh_n": len(fresh),
            }

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump({"list_as_of": list_as_of, "symbols": fresh}, fh, indent=2, sort_keys=True)
        fh.write("\n")
    return fresh, {"source": "fresh_fetch", "label": label, "n": len(fresh)}

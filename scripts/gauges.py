"""Pure calculation functions for the Market Conditions Brief.

No I/O here -- every function takes already-fetched pandas Series/dicts and
returns numbers or labels. fetch_conditions.py does the fetching and calls
into this module; tests/test_gauges.py exercises every band edge directly
against these functions.

Conventions (see the locked spec):
- Percentile rank = 100 * |{r in R : r <= x_now}| / |R|, R = trailing
  `years` window of PRIOR observations (excludes the latest value itself).
- Band edges are lower-bound-inclusive, upper-bound-exclusive; a value
  exactly on an edge takes the HIGHER band.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd


# ── series prep ──────────────────────────────────────────────────────────────

def drop_in_progress(series: pd.Series, run_date: dt.date) -> pd.Series:
    """Drop any bar dated on or after `run_date` -- the not-yet-completed
    session. Mandatory before every calculation below."""
    if series.empty:
        return series
    return series[series.index.date < run_date]


def max_consecutive_nan(raw_close: pd.Series) -> int:
    """Longest run of consecutive NaNs in the RAW (pre-dropna) close series --
    an integrity signal a clean dropna() would otherwise hide."""
    is_na = raw_close.isna()
    if not is_na.any():
        return 0
    groups = (~is_na).cumsum()
    run_lengths = is_na.groupby(groups).sum()
    return int(run_lengths.max()) if len(run_lengths) else 0


def max_gap_days(series: pd.Series, tail_n: int | None = None) -> int:
    """Largest calendar-day gap between consecutive observations (optionally
    restricted to the trailing `tail_n` bars) -- catches a hole in the feed
    that a plain rolling-window calc would silently paper over."""
    tail = series.iloc[-tail_n:] if tail_n else series
    if len(tail) < 2:
        return 0
    gaps = tail.index.to_series().diff().dropna().dt.days
    return int(gaps.max()) if len(gaps) else 0


# ── core statistics ──────────────────────────────────────────────────────────

def percentile_rank(series: pd.Series, years: int = 10):
    """(value, percentile, n_ref, as_of_date). percentile/n_ref are None if
    there is no reference window at all (caller treats that as short_history
    or, if value itself is missing, stale/fetch_failed)."""
    if series.empty:
        return None, None, 0, None
    now = float(series.iloc[-1])
    as_of = series.index[-1]
    cutoff = as_of - pd.DateOffset(years=years)
    ref = series[(series.index > cutoff) & (series.index < as_of)]
    if len(ref) == 0:
        return now, None, 0, as_of.date()
    pct = float((ref <= now).mean() * 100)
    return now, pct, len(ref), as_of.date()


def sma_distance(series: pd.Series, n: int = 200):
    """(close, sma, pct_distance, as_of_date). None fields if fewer than `n`
    observations exist yet (short_history)."""
    if len(series) < n:
        return None, None, None, (series.index[-1].date() if len(series) else None)
    sma_now = float(series.rolling(n).mean().iloc[-1])
    close = float(series.iloc[-1])
    if sma_now == 0:
        return close, sma_now, None, series.index[-1].date()
    dist = (close / sma_now - 1) * 100
    return close, sma_now, float(dist), series.index[-1].date()


def change_since(series: pd.Series, years: float = 1.0):
    """(now, base, pct_change, as_of_date) vs the last observation on or
    before `years` years ago. None fields if no such reference point exists
    yet (short_history)."""
    if series.empty:
        return None, None, None, None
    as_of = series.index[-1]
    cutoff = as_of - pd.DateOffset(years=years)
    ref = series[series.index <= cutoff]
    if ref.empty:
        return float(series.iloc[-1]), None, None, as_of.date()
    base = float(ref.iloc[-1])
    now = float(series.iloc[-1])
    if base == 0:
        return now, base, None, as_of.date()
    return now, base, (now / base - 1) * 100, as_of.date()


def breadth_pct(prices: dict[str, pd.Series], n: int = 200, max_gap_days_allowed: int = 8):
    """prices: {symbol: Close series, already in-progress-bar-dropped}.
    Eligible member = >= n completed sessions AND latest close within
    `max_gap_days_allowed` calendar days of the newest date seen across the
    whole set. Returns (pct_above_sma, n_eligible, n_total)."""
    if not prices:
        return None, 0, 0
    latest_dates = [s.index[-1] for s in prices.values() if len(s)]
    if not latest_dates:
        return None, 0, len(prices)
    reference_date = max(latest_dates)
    above = 0
    eligible = 0
    for s in prices.values():
        if len(s) < n:
            continue
        if (reference_date - s.index[-1]).days > max_gap_days_allowed:
            continue
        eligible += 1
        sma = s.rolling(n).mean().iloc[-1]
        if float(s.iloc[-1]) > float(sma):
            above += 1
    if eligible == 0:
        return None, 0, len(prices)
    return (above / eligible) * 100, eligible, len(prices)


# ── band mappings (lower-inclusive, upper-exclusive; tie -> higher band) ────

def _band_index_4(value: float) -> int:
    """Shared 4-band shape for the three percentile-style gauges
    (volatility/rates/oil): <25 / 25-75 / 75-90 / >=90."""
    if value < 25:
        return 0
    if value < 75:
        return 1
    if value < 90:
        return 2
    return 3


_PERCENTILE_LABELS = {
    "volatility": ["calm", "normal", "elevated", "extreme"],
    "rates": ["low", "mid_range", "high", "very_high"],
    "oil": ["low", "mid_range", "high", "very_high"],
}


def band_volatility(pct: float) -> str:
    return _PERCENTILE_LABELS["volatility"][_band_index_4(pct)]


def band_rates(pct: float) -> str:
    return _PERCENTILE_LABELS["rates"][_band_index_4(pct)]


def band_oil(pct: float) -> str:
    return _PERCENTILE_LABELS["oil"][_band_index_4(pct)]


def band_trend(dist_pct: float) -> str:
    if dist_pct < -10:
        return "well_below"
    if dist_pct < -3:
        return "below"
    if dist_pct < 3:
        return "near"
    if dist_pct < 10:
        return "above"
    return "well_above"


def band_breadth(pct_above: float) -> str:
    if pct_above < 30:
        return "narrow"
    if pct_above < 50:
        return "mixed"
    if pct_above < 75:
        return "broad"
    return "very_broad"


def band_dollar(pct_chg: float) -> str:
    if pct_chg < -8:
        return "much_weaker"
    if pct_chg < -3:
        return "weaker"
    if pct_chg < 3:
        return "steady"
    if pct_chg < 8:
        return "stronger"
    return "much_stronger"


# ── NO READ predicates ───────────────────────────────────────────────────────

NO_READ_REASONS = {
    "stale": "Data older than a week.",
    "short_history": "Not enough history yet.",
    "fetch_failed": "Source didn't answer this week.",
    "coverage": "Fewer than 90% of members loaded.",
    "integrity": "Numbers failed a sanity check.",
}


def is_stale_daily(as_of_date: dt.date, run_date: dt.date, max_calendar_days: int = 8) -> bool:
    """~5 trading days, expressed as an 8-calendar-day buffer to absorb a
    long weekend/holiday without false-flagging a genuinely fresh reading."""
    if as_of_date is None:
        return True
    return (run_date - as_of_date).days > max_calendar_days


def is_stale_monthly(period_end: dt.date, run_date: dt.date, max_days: int = 120) -> bool:
    if period_end is None:
        return True
    return (run_date - period_end).days > max_days


def has_short_history(n_ref: int | None, minimum: int) -> bool:
    return n_ref is None or n_ref < minimum

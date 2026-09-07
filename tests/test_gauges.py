"""Unit tests for scripts/gauges.py -- every band edge tested both
directions, every NO_READ predicate, and the in-progress-bar drop."""
import datetime as dt
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import gauges  # noqa: E402


# ── band edges: lower-inclusive, upper-exclusive; tie takes the HIGHER band ─

def test_band_index4_edges():
    # shared 4-band shape (volatility/rates/oil): cuts at 25 / 75 / 90
    assert gauges._band_index_4(24.999) == 0
    assert gauges._band_index_4(25.0) == 1
    assert gauges._band_index_4(74.999) == 1
    assert gauges._band_index_4(75.0) == 2
    assert gauges._band_index_4(89.999) == 2
    assert gauges._band_index_4(90.0) == 3


def test_band_volatility_labels():
    assert gauges.band_volatility(10) == "calm"
    assert gauges.band_volatility(25) == "normal"
    assert gauges.band_volatility(80) == "elevated"
    assert gauges.band_volatility(95) == "extreme"


def test_band_rates_labels():
    assert gauges.band_rates(10) == "low"
    assert gauges.band_rates(25) == "mid_range"
    assert gauges.band_rates(80) == "high"
    assert gauges.band_rates(95) == "very_high"


def test_band_oil_labels():
    assert gauges.band_oil(10) == "low"
    assert gauges.band_oil(25) == "mid_range"
    assert gauges.band_oil(80) == "high"
    assert gauges.band_oil(95) == "very_high"


def test_band_trend_edges():
    assert gauges.band_trend(-10.001) == "well_below"
    assert gauges.band_trend(-10.0) == "below"
    assert gauges.band_trend(-3.001) == "below"
    assert gauges.band_trend(-3.0) == "near"
    assert gauges.band_trend(2.999) == "near"
    assert gauges.band_trend(3.0) == "above"
    assert gauges.band_trend(9.999) == "above"
    assert gauges.band_trend(10.0) == "well_above"


def test_band_breadth_edges():
    assert gauges.band_breadth(29.999) == "narrow"
    assert gauges.band_breadth(30.0) == "mixed"
    assert gauges.band_breadth(49.999) == "mixed"
    assert gauges.band_breadth(50.0) == "broad"
    assert gauges.band_breadth(74.999) == "broad"
    assert gauges.band_breadth(75.0) == "very_broad"


def test_band_dollar_edges():
    assert gauges.band_dollar(-8.001) == "much_weaker"
    assert gauges.band_dollar(-8.0) == "weaker"
    assert gauges.band_dollar(-3.001) == "weaker"
    assert gauges.band_dollar(-3.0) == "steady"
    assert gauges.band_dollar(2.999) == "steady"
    assert gauges.band_dollar(3.0) == "stronger"
    assert gauges.band_dollar(7.999) == "stronger"
    assert gauges.band_dollar(8.0) == "much_stronger"


# ── core stats ───────────────────────────────────────────────────────────────

def _series(values, start="2016-01-01", freq="D"):
    idx = pd.date_range(start=start, periods=len(values), freq=freq)
    return pd.Series(values, index=idx)


def test_percentile_rank_excludes_latest_value_itself():
    # 100 prior obs all equal to 5, latest obs = 10 -> should rank at the top
    # (100th pct of PRIOR obs, since all 100 priors are <= 10).
    vals = [5.0] * 100 + [10.0]
    s = _series(vals)
    value, pct, n_ref, as_of = gauges.percentile_rank(s, years=10)
    assert value == 10.0
    assert pct == 100.0
    assert n_ref == 100


def test_percentile_rank_no_reference_window():
    s = _series([5.0])
    value, pct, n_ref, as_of = gauges.percentile_rank(s, years=10)
    assert pct is None
    assert n_ref == 0


def test_sma_distance_basic():
    vals = [100.0] * 199 + [110.0]  # 200th obs is the "close"
    s = _series(vals)
    close, sma, dist, as_of = gauges.sma_distance(s, n=200)
    assert close == 110.0
    expected_sma = (100.0 * 199 + 110.0) / 200
    assert abs(sma - expected_sma) < 1e-9
    assert dist > 0


def test_sma_distance_short_history():
    s = _series([100.0] * 50)
    close, sma, dist, as_of = gauges.sma_distance(s, n=200)
    assert close is None and sma is None and dist is None


def test_change_since_basic():
    idx = pd.date_range("2015-01-01", periods=800, freq="D")
    vals = [100.0] * 800
    s = pd.Series(vals, index=idx)
    s.iloc[-1] = 110.0  # today's value is 10% above a year ago
    now, base, chg, as_of = gauges.change_since(s, years=1.0)
    assert now == 110.0
    assert base == 100.0
    assert abs(chg - 10.0) < 1e-9


def test_change_since_no_reference_point():
    s = _series([100.0] * 30)
    now, base, chg, as_of = gauges.change_since(s, years=1.0)
    assert chg is None


def test_breadth_pct_basic():
    idx = pd.date_range("2015-01-01", periods=250, freq="D")
    above = pd.Series([100.0] * 249 + [200.0], index=idx)   # ends well above its SMA
    below = pd.Series([100.0] * 249 + [50.0], index=idx)    # ends well below its SMA
    too_short = pd.Series([100.0] * 50, index=idx[:50])     # ineligible
    prices = {"ABOVE": above, "BELOW": below, "SHORT": too_short}
    pct, n_eligible, n_total = gauges.breadth_pct(prices, n=200)
    assert n_eligible == 2
    assert n_total == 3
    assert abs(pct - 50.0) < 1e-9


def test_breadth_pct_empty():
    pct, n_eligible, n_total = gauges.breadth_pct({})
    assert pct is None and n_eligible == 0 and n_total == 0


# ── in-progress bar drop ────────────────────────────────────────────────────

def test_drop_in_progress_removes_todays_bar():
    today = dt.date(2026, 9, 7)
    idx = pd.date_range("2026-09-01", periods=7, freq="D")  # includes today
    s = pd.Series(np.arange(7, dtype=float), index=idx)
    out = gauges.drop_in_progress(s, today)
    assert (out.index.date < today).all()
    assert len(out) == 6


def test_drop_in_progress_empty_series():
    out = gauges.drop_in_progress(pd.Series(dtype=float), dt.date(2026, 9, 7))
    assert out.empty


# ── integrity / staleness / short-history predicates ────────────────────────

def test_is_stale_daily():
    run_date = dt.date(2026, 9, 7)
    assert gauges.is_stale_daily(dt.date(2026, 9, 5), run_date) is False   # 2 days old, fine
    assert gauges.is_stale_daily(dt.date(2026, 8, 20), run_date) is True   # long gap
    assert gauges.is_stale_daily(None, run_date) is True


def test_is_stale_monthly():
    run_date = dt.date(2026, 9, 7)
    assert gauges.is_stale_monthly(dt.date(2026, 7, 31), run_date) is False  # ~38 days
    assert gauges.is_stale_monthly(dt.date(2026, 1, 31), run_date) is True   # >120 days
    assert gauges.is_stale_monthly(None, run_date) is True


def test_has_short_history():
    assert gauges.has_short_history(2500, 2000) is False
    assert gauges.has_short_history(1999, 2000) is True
    assert gauges.has_short_history(None, 2000) is True


def test_max_consecutive_nan():
    s = pd.Series([1.0, np.nan, np.nan, np.nan, 2.0, np.nan, 3.0])
    assert gauges.max_consecutive_nan(s) == 3
    assert gauges.max_consecutive_nan(pd.Series([1.0, 2.0, 3.0])) == 0


def test_max_gap_days():
    idx = pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-20"])
    s = pd.Series([1.0, 2.0, 3.0], index=idx)
    assert gauges.max_gap_days(s) == 18

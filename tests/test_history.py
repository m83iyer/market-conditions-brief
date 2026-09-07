"""Regression test for build_history_series's point format.

Real bug caught by manual browser verification 2026-09-07: the scrubber's
replay used the stored number as if it were the gauge's raw value, but for
percentile-kind gauges (volatility/rates/oil) only the PERCENTILE was ever
stored -- so replaying, say, April 2021 showed "60.80" as the VIX level
(actually its 60.8th-percentile rank) and Brent at "$44.90" (actually its
44.9th percentile), both wrong by construction, not just wrong numbers.
Fixed by storing [date, raw_value, percentile] for percentile-kind gauges
and updating both the band/marker logic (uses percentile) and the display
value (uses raw_value) on the frontend to read the right slot. This test
locks the on-disk shape so the two can't drift apart again.
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import fetch_conditions as fc  # noqa: E402
import gauges  # noqa: E402


def _daily_series(n_days=3000, start="2016-01-01", value_fn=None):
    idx = pd.date_range(start=start, periods=n_days, freq="D")
    if value_fn is None:
        value_fn = lambda i: 20.0 + (i % 50)  # noqa: E731
    return pd.Series([value_fn(i) for i in range(n_days)], index=idx)


def test_percentile_kind_returns_three_element_points_with_consistent_percentile():
    s = _daily_series()
    history = fc.build_history_series("percentile", s)
    assert history, "expected at least one weekly point"
    for point in history:
        assert len(point) == 3, f"percentile-kind history point must be [date, value, pct], got {point}"
        date_str, value, pct = point
        assert isinstance(value, float)
        assert 0.0 <= pct <= 100.0
        # Recompute independently, as-of that same date, and confirm it
        # matches what build_history_series stored -- no lookahead bias.
        as_of_slice = s[s.index <= pd.Timestamp(date_str)]
        _, expected_pct, _, _ = gauges.percentile_rank(as_of_slice, years=10)
        if expected_pct is not None:
            assert abs(expected_pct - pct) < 0.15, (date_str, expected_pct, pct)


def test_trend_kind_returns_two_element_points():
    s = _daily_series(n_days=1200, value_fn=lambda i: 100.0 + i * 0.01)
    history = fc.build_history_series("trend", s)
    assert history
    for point in history:
        assert len(point) == 2, f"trend-kind history point must be [date, value], got {point}"


def test_dollar_kind_returns_two_element_points():
    s = _daily_series(n_days=1200, value_fn=lambda i: 100.0 + (i % 30))
    history = fc.build_history_series("dollar", s)
    assert history
    for point in history:
        assert len(point) == 2, f"dollar-kind history point must be [date, value], got {point}"


def test_percentile_history_not_lookahead_biased():
    """A change in data AFTER a given week must not change that week's
    already-recorded percentile -- the exact class of bug a naive
    "compute once over the whole series" implementation would produce."""
    base = _daily_series(n_days=2600, value_fn=lambda i: 20.0 + (i % 40))
    h1 = fc.build_history_series("percentile", base)

    extended = pd.concat([
        base,
        pd.Series([500.0] * 30, index=pd.date_range(
            start=base.index[-1] + pd.Timedelta(days=1), periods=30, freq="D")),
    ])
    h2 = fc.build_history_series("percentile", extended)

    h1_by_date = {p[0]: p[2] for p in h1}
    h2_by_date = {p[0]: p[2] for p in h2}
    shared_dates = set(h1_by_date) & set(h2_by_date)
    assert shared_dates, "expected overlapping weekly dates between the two runs"
    for d in shared_dates:
        assert abs(h1_by_date[d] - h2_by_date[d]) < 0.15, (
            f"{d}: percentile changed from {h1_by_date[d]} to {h2_by_date[d]} "
            "after appending FUTURE data -- lookahead bias"
        )

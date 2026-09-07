# Market Conditions Brief

Six things about US and India markets, each measured on its own: how jumpy
it is, where the index sits versus its own 200-day average, how many
stocks are moving with it, and the rates, dollar and oil backdrop around it.

**Live: https://m83iyer.github.io/market-conditions-brief/**

There is no combined number here, on purpose. Each gauge answers one narrow,
objective question from public data and states where today's reading falls
against its own trailing 10-year history. When a gauge cannot honestly be
computed for a given week (stale data, not enough history, a source that
didn't answer), it says so plainly instead of guessing.

## The six gauges

**Stock market**
- **Volatility** — the VIX (India VIX for the India view), ranked against its own trailing 10-year distribution.
- **Trend** — the index versus its 200-day moving average.
- **Breadth** — the share of index members trading above their own 200-day average.

**Macro backdrop**
- **Rates** — the 10-year government bond yield, ranked against its own trailing 10-year distribution (US: Treasury yield; India: OECD's long-term rate series).
- **Dollar** — how much the dollar has moved over the past year (US: against a basket of major currencies; India: against the rupee).
- **Oil** — Brent crude, ranked against its own trailing 10-year distribution. Same reading in both views — it's a global input, not a market-specific one.

Every card names its own data source and the date of its own latest reading.

## How it's built

- `scripts/fetch_conditions.py` pulls each gauge from Yahoo Finance (via `yfinance`) or the OECD's public data API, computes its reading with `scripts/gauges.py`, and writes everything to `docs/data/`.
- `docs/index.html` + `docs/style.css` + `docs/app.js` are a single static page — no backend, no build step, no framework.
- `.github/workflows/refresh-data.yml` re-runs the fetch every week and commits the update automatically.
- `tests/test_gauges.py` checks every band boundary in both directions and every no-read reason with synthetic fixtures. `tests/test_bright_line.py` scans the shipped page for any wording that would turn this into advice, a forecast, or a single combined reading — and fails the build if it finds one.
- Hosted on GitHub Pages directly from `docs/`.

## Run locally

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python scripts/fetch_conditions.py   # populates docs/data/
python3 -m http.server 8000 --directory docs
pytest
```

Measured conditions, not advice. Data can lag by up to a week between refreshes.

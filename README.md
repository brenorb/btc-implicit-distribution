# btc-implicit-distribution

A static GitHub Pages explorer for the market-implied BTC price distribution derived from Deribit option quotes.

## What it does

- Fetches live BTC option summaries from the public Deribit API
- Uses local browser cache with a short TTL to avoid hammering the public Deribit API
- Rebuilds the strike grid with linear interpolation when needed
- Estimates a risk-neutral price distribution from finite-difference butterflies
- Runs the Python calculation in the browser with Pyodide
- Renders a friendly interface for expiry selection and parameter tuning

## Local development

This project is intentionally static. Any simple HTTP server works.

```bash
cd /Users/breno/Documents/code/PROJECTS/btc-implicit-distribution
python3 -m http.server 4173
```

Then open `http://localhost:4173`.

## Tests

The pricing engine is pure Python and tested with `pytest`.

```bash
cd /Users/breno/Documents/code/PROJECTS/btc-implicit-distribution
uv run --with pytest pytest
```

## Notes

- The chart is a risk-neutral market-implied distribution, not an oracle.
- Deribit quote conventions require multiplying option prices by the underlying level before extracting butterfly probabilities. This repo preserves that adjustment from the original notebook work.
- The site is designed to work without any backend.

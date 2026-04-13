# meridian web dashboard

Zero-build interactive simulator that runs the same conceptual model as
`src/agents/rwa_market_maker.py` in the browser. Pick an asset class
(private credit, T-bills, real estate, long-tail), choose a strategy
(`constant_spread` or `adaptive_spread`), tweak the market regime, watch
quotes / fills / inventory / PnL evolve.

## Run locally

```bash
python3 -m http.server -d web 8080
# open http://localhost:8080
```

## Hosted

- kcolbchain.com/meridian/

## Out of scope (handled by the Python agent, not this dashboard)

- Real venue connectivity (live order books).
- Backtesting against historical data — see `src/backtest/engine.py`.
- Compliance-gated quoting (per-counterparty bid/ask) — depends on
  `kcolbchain/rwa-toolkit` and is tracked as a strategy stub on the
  dashboard ("compliance_gated", "geo_priced").

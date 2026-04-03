# agent-amm

Autonomous market-making agents for RWA and long-tail assets — by [kcolbchain](https://kcolbchain.com) (est. 2015).

## The Problem

Current AMM models (constant product, concentrated liquidity) were designed for liquid, fungible tokens with continuous price discovery. They fail for real-world assets:

- **Illiquid** — RWA trades are infrequent, thin order books
- **Irregular pricing** — real estate, commodities, private credit don't have second-by-second price feeds
- **Geography-specific** — the same asset class prices differently across jurisdictions
- **Compliance-gated** — not every counterparty can trade every asset

Constant product AMMs bleed capital in these conditions. LPs get destroyed by informed flow. Spreads are either too wide (no trades) or too tight (adverse selection).

## The Solution

Autonomous agents that manage liquidity positions intelligently:

- **Oracle-driven pricing** — agents price based on real-world signals, not just on-chain pool state
- **Adaptive spreads** — widen in volatility, narrow in stability, adjust for inventory risk
- **Geography-aware** — pricing adjustments per jurisdiction
- **Inventory management** — agents rebalance to avoid directional exposure
- **Backtestable** — every strategy can be tested against historical data before deployment

## Architecture

```
┌─────────────────────────────────────┐
│            Agent Framework          │
├──────────┬──────────┬───────────────┤
│ Strategies│  Oracle  │  Backtest    │
│          │  Feeds   │  Engine      │
├──────────┴──────────┴───────────────┤
│         Position & Risk Mgmt       │
├─────────────────────────────────────┤
│      Chain Connectors (EVM)        │
└─────────────────────────────────────┘
```

## Getting Started

```bash
git clone https://github.com/kcolbchain/agent-amm.git
cd agent-amm
pip install -r requirements.txt

# Run with mock data
python -m src.agents.rwa_market_maker --config config/default.yaml --simulate

# Backtest a strategy
python -m src.backtest.engine --strategy adaptive_spread --data data/sample.csv
```

## Strategies

| Strategy | Description |
|----------|-------------|
| `constant_spread` | Fixed bid/ask spread — baseline strategy |
| `adaptive_spread` | Spread adjusts to volatility + inventory exposure |

Build your own by extending `BaseStrategy` in `src/strategies/`.

## Project Structure

```
src/
  agents/          — Agent implementations
    base_agent.py  — Abstract base agent
    rwa_market_maker.py — RWA-specific market maker
  strategies/      — Pluggable trading strategies
  oracle/          — Price feed integrations
  backtest/        — Backtesting engine
  utils/           — Config, logging, helpers
config/            — YAML configuration files
tests/             — Test suite
```

## Contributing

We welcome contributions. See open issues tagged `good-first-issue` for starting points.

1. Fork the repo
2. Create a feature branch
3. Submit a PR with tests

## License

MIT — see [LICENSE](LICENSE)

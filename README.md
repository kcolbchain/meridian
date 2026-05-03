# meridian

RWA market-making stack — ERC-4626 LP vaults, agent-driven quoting, oracle-adapted pricing, compliance-gated execution. By [kcolbchain](https://kcolbchain.com) (est. 2015).

The full on-chain stack: contracts + agent + risk + execution. Built on top of [`kcolbchain/quoter`](https://github.com/kcolbchain/quoter) (the strategy core).

## The problem

AMMs designed for liquid token pairs fail on real-world assets:

- **Illiquid** — RWA trades are infrequent; CFMM math bleeds against thin flow.
- **Irregular pricing** — real-estate, private credit, T-bills don't have second-by-second feeds.
- **Geography-specific** — the same asset prices differently across jurisdictions.
- **Compliance-gated** — not every counterparty can trade every asset.

Constant-product LPs get destroyed. Spreads are too wide (no fills) or too tight (adverse selection).

## What meridian ships

- **ERC-4626 LP vault** (`MeridianVaultERC4626.sol`) — third-party LPs deposit a base asset (e.g. USDC), receive shares, passively earn the agent's quoting profit. High-water-mark performance fee + linearly accruing management fee, both configurable, both safety-capped.
- **Agent-controlled vault** (`MeridianVault.sol`) — circuit-breaker, slippage caps, allow-listed routers. The agent has exclusive trade-execution rights; deposits are isolated from agent keys.
- **Strategy executor + oracle adapter** (`StrategyExecutor.sol`, `OracleAdapter.sol`) — on-chain hooks for the agent's pricing decisions.
- **Agent runtime** (`src/agents/`) — `rwa_market_maker.py`, `ml_pricing_agent.py`, plus the strategies inherited from quoter.
- **EVM connectivity + execution** (`src/chains/evm/`, `src/execution/{uniswap_v3,vault_executor}.py`) — live + dry-run pathways.
- **Risk manager** (`src/risk/risk_manager.py`) — inventory-time-weighted caps, position-aware throttling.
- **Backtest engine** — replay historical or simulated tape against any strategy before going live.
- **Browser simulator** (`web/`) — pick an asset class, choose a strategy, watch quotes / fills / inventory / PnL evolve. Hosted at [kcolbchain.com/meridian/](https://kcolbchain.com/meridian/).

## Architecture

```
                       ┌──────────────────────────────┐
   LP USDC  ──────────►│   MeridianVaultERC4626       │
                       │   (4626 shares + fees)       │
                       └──────────┬───────────────────┘
                                  │ withdraw/redeem
                                  ▼
                       ┌──────────────────────────────┐
   Agent  ◄────────────┤   MeridianVault              │
   (owner)             │   slippage cap + circuit-bk  │
                       └──────────┬───────────────────┘
                                  │ approved routers
                                  ▼
        Oracle ─► OracleAdapter ─► StrategyExecutor ─► DEX
                                  ▲
                Risk manager ─────┤
                                  ▲
                Strategies (quoter) ──── adaptive / constant / your own
```

LPs deposit. Agent quotes. Oracle drives fair value. Risk manager throttles. Vault enforces slippage + router allow-list. Strategy from `quoter` decides the prices.

## Quick start

```bash
git clone https://github.com/kcolbchain/meridian.git
cd meridian

# Python agent
pip install -r requirements.txt
python -m src.agents.rwa_market_maker --config config/default.yaml --simulate

# Contracts (Hardhat)
npm install
npm run compile
npm run test

# Deploy to testnets
npm run deploy:base-sepolia
npm run deploy:op-sepolia
npm run deploy:fuji

# Browser simulator
python3 -m http.server -d web 8080  # then open http://localhost:8080
```

## Live demo

The browser simulator at [kcolbchain.com/meridian/](https://kcolbchain.com/meridian/) runs the same conceptual model as the Python agent — pick an asset class, tune the regime, watch quotes evolve.

## Where this fits

- **Strategy core** — meridian re-uses [`kcolbchain/quoter`](https://github.com/kcolbchain/quoter)'s strategies and backtest engine. If you want pure venue-agnostic quoting without the on-chain stack, start there.
- **Stablecoin issuance for the asset under management** — see [`kcolbchain/stablecoin-toolkit`](https://github.com/kcolbchain/stablecoin-toolkit).
- **Pre-audit hardening before mainnet** — see [`kcolbchain/audit-checklist`](https://github.com/kcolbchain/audit-checklist).

## Status

- **Contracts:** written, Hardhat tests passing, testnet deploys configured (Base Sepolia, OP Sepolia, Fuji). Not audited; do not run on mainnet without a formal audit.
- **Agent runtime:** simulate mode + risk manager + backtest. Live execution path is wired through `vault_executor.py` but expects venue config the operator supplies.
- **Browser sim:** shipped. Strategy stubs for `compliance_gated` / `geo_priced` are tracked as roadmap; not yet wired to on-chain transfer-restriction adapters.

## Running the tests

```bash
# Python
pytest -q

# Solidity
npm run test
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) and [CONTRIBUTORS.md](CONTRIBUTORS.md). Issues tagged `good-first-issue` are great entry points. The `MeridianVaultERC4626` invariant + fuzz suite ([#31](https://github.com/kcolbchain/meridian/issues/31)) is the highest-leverage open issue right now.

## Working with kcolbchain

We build, deploy, and operate RWA market-making for partner protocols — managed liquidity-on-call, pre-audit, integration, and operational coverage. If you'd like to talk, see [kcolbchain.com/work-with-us](https://kcolbchain.com/work-with-us/).

## Links

- **Docs:** https://docs.kcolbchain.com/meridian/
- **All projects:** https://docs.kcolbchain.com/
- **kcolbchain:** https://kcolbchain.com

## License

MIT — see [LICENSE](LICENSE)

---

*Founded by [Abhishek Krishna](https://abhishekkrishna.com) • GitHub: [@abhicris](https://github.com/abhicris)*

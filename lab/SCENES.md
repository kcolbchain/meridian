# Meridian lab — scene contract

`lab/index.html` is a single-file canvas demo of the Meridian RWA market-making agent. No build step. Add a scene by appending one object to the `SCENES` array.

The lab is structurally identical to the [Mixdown lab](https://github.com/kcolbchain/muzix/blob/main/web/public/mixdown.html) in the muzix repo — same canvas engine, transport bar, tooltip, scene pill bar — but with a Meridian palette (cyan-primary, gold-secondary) and a different agent registry tuned for MM mechanics rather than royalty settlement.

## Scene shape

```js
SCENES.push({
  name: "Your scene title",
  captions: [
    "Markdown-lite caption for step 0 (intro state)",
    "Caption for step 1 — describe the on-chain action with <code>contractCall</code>",
    "Caption for step 2",
    // ...
  ],
  setup() {
    // Place agents on the canvas. cx, cy = stage center.
    const cx = W / 2, cy = H / 2 - 20;
    geoLanes = null; // or set to draw US/EM/Restricted bands — see scenes 2-3
    agents = {
      mm:     makeAgent({ id: "mm", name: "MM agent", role: "Meridian", type: "agent", x: cx - 300, y: cy }),
      pool:   makeAgent({ id: "pool", name: "CR8/RWA pool", role: "what it does", type: "pool", x: cx, y: cy }),
    };
  },
  steps: [
    () => { /* step 0: idle intro, no animation */ },
    (now) => {
      addTx({ from: "mm", to: "pool", color: "accent", label: "postQuote", dur: 1500 });
      byId("pool").flash = 1;
      // schedule follow-ups with setTimeout if you need staggered effects
    },
    // ...
  ],
});
```

The number of `captions` should match the number of `steps`. The transport auto-advances every `STEP_INTERVAL` (default 1.8s) while playing; users can also `space` (play/pause), `n` (step), `r` (restart), or `←`/`→` to jump scenes.

## Agent types (color / shape defaults)

| type | shape | color | use for |
|---|---|---|---|
| `oracle` | diamond | purple (dsp) | Chainlink, rates feed, custodian attestations |
| `agent` | hex | cyan (accent) | MM agent, `OracleAdapter`, `StrategyExecutor`, `MeridianVault`, `RiskManager`, resolver — anything kcolbchain-side |
| `pool` | hex | gold (signal) | the CR8-USD / RWA AMM pool |
| `venue` | square | orange (warn) | external venues — RFQ desks, takers, sister AMMs, OTC blocks |
| `lucidly` | circle | green (ok) | Lucidly syUSD idle-yield destination |
| `geo` | circle | white (wallet) | geography lane labels (US, EM) in report views |
| `wallet` | circle | white (wallet) | generic owner / page recipient / quote-intent placeholder |

Override `r`, `color`, or `shape` directly in `makeAgent({...})` if a scene needs something custom — see scene 7's `chainlink` (oracle re-colored hot-red to signal staleness) or scene 2's `qR` (hot-red gated quote).

## Transaction colors

`addTx({ from, to, color, label, dur, curve, dotSize })`

| color key | use for |
|---|---|
| `accent` (cyan) | agent-initiated tx — `postQuote`, `parkIdle`, `rebalance` |
| `signal` (gold) | contract-to-contract reads, vault hooks, `executeTrade` callbacks |
| `dsp` (purple) | oracle feed pushes into the adapter |
| `ok` (green) | money flow — fills, idle-yield park, payouts in the report |
| `warn` (orange) | venue-initiated tx — taker fills, external-venue legs in a rebalance |
| `hot` (red) | circuit-breaker / staleness / drift-over-cap signals; halts |
| `muted` | low-emphasis tx (cancellations, withdrawals) |

`curve` is a perpendicular offset (px) for parallel paths — use ±20-40 when multiple txs share the same endpoints so they don't overlap (see the oracle waterfall and the report fan-out).

## Geography lanes

Two scenes (2 and 3) use horizontal bands to anchor US / EM / Restricted lanes. Set `geoLanes` in `setup()`:

```js
geoLanes = {
  xLeft: 60, xRight: W - 60,
  lanes: [
    { label: "US lane",     sub: "spread ×1.00 = 5 bps",  y: cy - 160, color: "ok" },
    { label: "EM lane",     sub: "spread ×1.20 = 30 bps", y: cy,       color: "signal" },
    { label: "Restricted",  sub: "sanctions-flagged",     y: cy + 160, color: "hot", dim: true },
  ],
};
```

`dim: true` halves the band's saturation — use it for gated / inactive lanes so the eye reads them as off. Reset with `geoLanes = null` in scenes that don't need lanes.

## Step pacing rules of thumb

- Keep each step's effect under ~2s. The auto-advance interval is 1.8s; if you need longer, increase `STEP_INTERVAL` or split into two steps.
- Use `setTimeout` within a step for staggered fan-outs — e.g. the oracle waterfall (scene 1), the rebalance fan-out (scene 5), the report fan (scene 8).
- At each step boundary, all agents' `flash` decays by 70% and `glow` by 40%, so use `byId("x").glow = 1` to *sustain* attention across steps (e.g. the circuit-breaker pause).

## Naming style

Match the existing voice: short verb-led titles ("Quotes go out", "Fill arrives", "Idle yield park"), captions that read like a trader's running narration plus the concrete contract call in `<code>`. Plausible MM numbers (5 bps US, 30 bps EM, 3σ trigger, 7500 bps confidence floor, ~4.8% syUSD APY). The goal is for an RWA desk reviewer to follow the on-chain flow without prior protocol context.

## What the existing 8 scenes show

1. **Oracle waterfall** — Chainlink + rates + custodian → `OracleAdapter` → MM agent. Three feeds, one normalized tick.
2. **Fair value & spread** — `mid = oracle-weighted`, `spread = base · geo_multiplier`. US 5 bps, EM 30 bps, Restricted null.
3. **Quotes go out** — US lane to the CR8-USD/RWA AMM, EM lane to an RFQ desk, Restricted lane stays dark. `StrategyExecutor.postQuote`.
4. **Fill arrives** — US taker hits the bid, `MeridianVault.executeTrade`, agent updates internal mid via inventory skew.
5. **Inventory rebalance** — drift > +10% trips `RiskManager`; agent routes through a resolver to a sister AMM and OTC RFQ to bring inventory back inside ±5%.
6. **Idle yield park** — excess CR8-USD parked into Lucidly syUSD via `StrategyExecutor.parkIdle`. syUSD shares returned to the vault, accruing ~4.8% APY.
7. **Circuit breaker** — Chainlink confidence drops below 7500 bps *and* mid moves 3.4σ. `MeridianVault.setCircuitBreaker(true)`. Quotes pulled, owner paged.
8. **End-of-cycle report** — Treasury snapshot, P&L per lane (US +$1,142, EM +$2,386), idle yield (+$184), NAV +0.37%.

Each scene's caption stack maps step-by-step to the agent's decision loop in `src/agents/rwa_market_maker.py` and the contract surface in `contracts/{MeridianVault, OracleAdapter, StrategyExecutor}.sol`.

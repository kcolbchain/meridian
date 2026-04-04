"""Compare Avellaneda-Stoikov strategy against a simple constant-spread baseline.

Usage:
    python -m src.backtest.compare_strategies [--ticks 500] [--base-price 100]
"""

import random
import logging

from ..agents.rwa_market_maker import RWAMarketMaker
from ..agents.avellaneda_stoikov_agent import AvellanedaStoikovAgent
from .engine import BacktestEngine, BacktestResult

logger = logging.getLogger(__name__)


def _print_result(label: str, result: BacktestResult) -> None:
    print(f"\n{'=' * 50}")
    print(f"  {label}")
    print(f"{'=' * 50}")
    print(f"  Ticks:        {result.total_ticks}")
    print(f"  Fills:        {result.total_fills} ({result.fill_rate:.1%} fill rate)")
    print(f"  Realized PnL: {result.realized_pnl:>10.2f}")
    print(f"  Unrealized:   {result.unrealized_pnl:>10.2f}")
    print(f"  Total PnL:    {result.total_pnl:>10.2f}")
    print(f"  Max Drawdown: {result.max_drawdown:>10.2f}")
    print(f"  Sharpe Ratio: {result.sharpe_ratio:>10.3f}")
    print(f"  Final Pos:    {result.final_position:>10.4f}")


def run_comparison(
    ticks: int = 500,
    base_price: float = 100.0,
    volatility: float = 0.02,
    fill_prob: float = 0.4,
    seed: int = 42,
) -> dict[str, BacktestResult]:
    """Run both strategies on identical data and return results."""
    data = BacktestEngine.generate_mock_data(base_price, ticks, volatility)

    # --- Constant-spread baseline (via RWAMarketMaker) ---
    baseline_config = {
        "initial_quote": 10000,
        "base_spread_bps": 200,
        "max_order_size_pct": 0.1,
        "max_exposure": 50,
        "max_inventory_pct": 0.3,
    }
    random.seed(seed)
    baseline_agent = RWAMarketMaker("baseline-rwa", baseline_config)
    baseline_engine = BacktestEngine(baseline_agent, fill_probability=fill_prob)
    baseline_result = baseline_engine.run(data)

    # --- Avellaneda-Stoikov ---
    as_config = {
        "initial_quote": 10000,
        "risk_aversion": 0.1,
        "volatility_window": 50,
        "horizon": 1.0,
        "order_arrival_intensity": 1.5,
        "position_limit": 50,
        "order_size_pct": 0.1,
        "max_exposure": 50,
        "total_ticks": ticks,
    }
    random.seed(seed)
    as_agent = AvellanedaStoikovAgent("as-mm", as_config)
    as_engine = BacktestEngine(as_agent, fill_probability=fill_prob)
    as_result = as_engine.run(data)

    return {"baseline": baseline_result, "avellaneda_stoikov": as_result}


if __name__ == "__main__":
    import click

    @click.command()
    @click.option("--ticks", default=500, help="Number of backtest ticks")
    @click.option("--base-price", default=100.0, help="Starting mid price")
    @click.option("--volatility", default=0.02, help="Per-tick volatility")
    @click.option("--fill-prob", default=0.4, help="Simulated fill probability")
    @click.option("--seed", default=42, help="Random seed for reproducibility")
    def main(ticks, base_price, volatility, fill_prob, seed):
        logging.basicConfig(level=logging.WARNING)
        results = run_comparison(ticks, base_price, volatility, fill_prob, seed)
        _print_result("Baseline (Constant Spread / RWA MM)", results["baseline"])
        _print_result("Avellaneda-Stoikov Optimal MM", results["avellaneda_stoikov"])

    main()

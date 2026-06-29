"""Backtest performance metrics.

Pure functions for evaluating a backtest run. Kept dependency-light
(numpy only) and side-effect free so they can be unit-tested against
known numeric vectors and reused outside the engine.
"""

from __future__ import annotations

from typing import Sequence

import numpy as np


def sharpe_ratio(
    returns: Sequence[float],
    risk_free_rate: float = 0.0,
    periods_per_year: float = 1.0,
) -> float:
    """Annualised Sharpe ratio of a per-period return series.

    Computed as ``mean(excess) / std(excess) * sqrt(periods_per_year)``
    where ``excess = returns - risk_free_rate`` and the standard
    deviation is the population std (ddof=0).

    Returns ``0.0`` when there are fewer than two returns or when the
    excess-return volatility is zero (no dispersion -> undefined ratio).

    Args:
        returns: Per-period (e.g. per-tick) returns.
        risk_free_rate: Per-period risk-free rate, same units as returns.
        periods_per_year: Scaling factor to annualise (e.g. 252 for daily).
    """
    arr = np.asarray(returns, dtype=float)
    if arr.size < 2:
        return 0.0

    excess = arr - risk_free_rate
    std = float(np.std(excess))
    if std == 0.0:
        return 0.0

    return float(np.mean(excess) / std * np.sqrt(periods_per_year))


def max_drawdown(equity_curve: Sequence[float]) -> float:
    """Maximum peak-to-trough drop of an equity / PnL curve.

    Returns a non-negative magnitude in the same units as the curve:
    the largest ``running_peak - value`` observed. A monotonically
    non-decreasing curve has a drawdown of ``0.0``. An empty or
    single-point curve also returns ``0.0``.
    """
    arr = np.asarray(equity_curve, dtype=float)
    if arr.size < 2:
        return 0.0

    running_peak = np.maximum.accumulate(arr)
    drawdowns = running_peak - arr
    return float(np.max(drawdowns))


def inventory_time_weighted_pnl(
    pnl_curve: Sequence[float],
    inventory_curve: Sequence[float],
) -> float:
    """Inventory-time-weighted PnL.

    Weights each step's PnL increment by the absolute inventory held
    over that step, so PnL earned while carrying a large position counts
    for more than PnL earned while flat. This surfaces whether returns
    are compensation for taking on inventory risk.

    The PnL increment for step ``i`` is ``pnl[i] - pnl[i-1]`` and the
    inventory held across that step is taken as ``inventory[i-1]`` (the
    position carried into the step). The result is the sum of
    ``abs(inventory[i-1]) * (pnl[i] - pnl[i-1])`` over all steps.

    Both inputs must be the same length. Curves shorter than two points
    yield ``0.0`` (no completed step).

    Args:
        pnl_curve: Cumulative PnL sampled at each step.
        inventory_curve: Inventory (base position) sampled at each step,
            aligned with ``pnl_curve``.
    """
    pnl = np.asarray(pnl_curve, dtype=float)
    inv = np.asarray(inventory_curve, dtype=float)

    if pnl.shape != inv.shape:
        raise ValueError(
            f"pnl_curve and inventory_curve must have the same length: "
            f"{pnl.shape[0] if pnl.ndim else 0} != "
            f"{inv.shape[0] if inv.ndim else 0}"
        )
    if pnl.size < 2:
        return 0.0

    pnl_increments = np.diff(pnl)
    weights = np.abs(inv[:-1])
    return float(np.sum(weights * pnl_increments))

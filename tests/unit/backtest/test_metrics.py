"""Unit tests for backtest performance metrics against known vectors."""

import math

import numpy as np
import pytest

from src.backtest.metrics import (
    inventory_time_weighted_pnl,
    max_drawdown,
    sharpe_ratio,
)


# --------------------------------------------------------------------------- #
# Sharpe ratio
# --------------------------------------------------------------------------- #
def test_sharpe_known_vector():
    returns = [0.01, 0.02, 0.01, 0.03, 0.02]
    # mean / population-std, no annualisation
    expected = float(np.mean(returns) / np.std(returns))
    assert sharpe_ratio(returns) == pytest.approx(expected)


def test_sharpe_subtracts_risk_free_rate():
    returns = [0.05, 0.05, 0.05, 0.05]
    # constant returns equal to rf -> zero excess -> zero std -> 0.0
    assert sharpe_ratio(returns, risk_free_rate=0.05) == 0.0


def test_sharpe_annualisation_scales_by_sqrt_periods():
    returns = [0.01, -0.005, 0.02, 0.0, 0.015, -0.01]
    base = sharpe_ratio(returns)
    annualised = sharpe_ratio(returns, periods_per_year=252)
    assert annualised == pytest.approx(base * math.sqrt(252))


def test_sharpe_zero_volatility_returns_zero():
    assert sharpe_ratio([0.02, 0.02, 0.02]) == 0.0


def test_sharpe_too_few_points_returns_zero():
    assert sharpe_ratio([]) == 0.0
    assert sharpe_ratio([0.01]) == 0.0


def test_sharpe_negative_mean_is_negative():
    returns = [-0.02, -0.01, -0.03, -0.015]
    assert sharpe_ratio(returns) < 0.0


# --------------------------------------------------------------------------- #
# Max drawdown
# --------------------------------------------------------------------------- #
def test_max_drawdown_known_vector():
    curve = [100, 120, 90, 110, 80, 130]
    # peaks: 100,120,120,120,120,130 -> drawdowns 0,0,30,10,40,0 -> 40
    assert max_drawdown(curve) == pytest.approx(40.0)


def test_max_drawdown_monotonic_increase_is_zero():
    assert max_drawdown([1, 2, 3, 4, 5]) == 0.0


def test_max_drawdown_recovers_then_dips_again():
    curve = [0, 50, 10, 60, 20]
    # peaks: 0,50,50,60,60 -> drawdowns 0,0,40,0,40 -> 40
    assert max_drawdown(curve) == pytest.approx(40.0)


def test_max_drawdown_all_negative_curve():
    curve = [-10, -5, -30, -20]
    # peaks: -10,-5,-5,-5 -> drawdowns 0,0,25,15 -> 25
    assert max_drawdown(curve) == pytest.approx(25.0)


def test_max_drawdown_short_curve_is_zero():
    assert max_drawdown([]) == 0.0
    assert max_drawdown([42.0]) == 0.0


# --------------------------------------------------------------------------- #
# Inventory-time-weighted PnL
# --------------------------------------------------------------------------- #
def test_itw_pnl_known_vector():
    pnl = [0, 10, 5, 20]
    inventory = [2, -3, 4, 1]
    # increments [10,-5,15], weights abs(inv[:-1]) = [2,3,4]
    # 2*10 + 3*-5 + 4*15 = 20 - 15 + 60 = 65
    assert inventory_time_weighted_pnl(pnl, inventory) == pytest.approx(65.0)


def test_itw_pnl_zero_inventory_yields_zero():
    pnl = [0, 5, 10, 7]
    inventory = [0, 0, 0, 0]
    assert inventory_time_weighted_pnl(pnl, inventory) == 0.0


def test_itw_pnl_uses_carried_inventory_not_post_step():
    # Holding 0 into the only step -> no weight regardless of pnl gain.
    assert inventory_time_weighted_pnl([0, 100], [0, 50]) == 0.0
    # Holding 50 into the step -> 50 * 100 = 5000.
    assert inventory_time_weighted_pnl([0, 100], [50, 0]) == pytest.approx(5000.0)


def test_itw_pnl_short_inventory_uses_absolute_value():
    # Negative (short) inventory should still weight positively by magnitude.
    assert inventory_time_weighted_pnl([0, 10], [-4, 0]) == pytest.approx(40.0)


def test_itw_pnl_length_mismatch_raises():
    with pytest.raises(ValueError):
        inventory_time_weighted_pnl([0, 1, 2], [0, 1])


def test_itw_pnl_short_curve_is_zero():
    assert inventory_time_weighted_pnl([], []) == 0.0
    assert inventory_time_weighted_pnl([5.0], [3.0]) == 0.0

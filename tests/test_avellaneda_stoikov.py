"""Tests for the Avellaneda-Stoikov strategy and agent."""

import math

import pytest
import numpy as np

from src.strategies.avellaneda_stoikov import (
    AvellanedaStoikovParams,
    VolatilityEstimator,
    reservation_price,
    optimal_spread,
    compute_quotes,
)
from src.agents.avellaneda_stoikov_agent import AvellanedaStoikovAgent
from src.agents.base_agent import Fill, Side


# ---------------------------------------------------------------------------
# Pure model functions
# ---------------------------------------------------------------------------

class TestReservationPrice:
    def test_zero_inventory(self):
        """With no inventory the reservation price equals the mid."""
        r = reservation_price(mid=100.0, inventory=0, risk_aversion=0.1,
                              volatility=0.02, time_remaining=1.0)
        assert r == pytest.approx(100.0)

    def test_long_inventory_shifts_down(self):
        """Positive inventory pushes the reservation price below mid."""
        r = reservation_price(mid=100.0, inventory=10, risk_aversion=0.1,
                              volatility=0.02, time_remaining=1.0)
        assert r < 100.0

    def test_short_inventory_shifts_up(self):
        """Negative inventory pushes the reservation price above mid."""
        r = reservation_price(mid=100.0, inventory=-10, risk_aversion=0.1,
                              volatility=0.02, time_remaining=1.0)
        assert r > 100.0

    def test_formula_exact(self):
        """Verify exact formula: r = s - q*γ*σ²*(T-t)."""
        s, q, gamma, sigma, tau = 100.0, 5.0, 0.3, 0.05, 0.8
        expected = s - q * gamma * sigma**2 * tau
        r = reservation_price(s, q, gamma, sigma, tau)
        assert r == pytest.approx(expected)

    def test_zero_time_remaining(self):
        """At horizon end the reservation price equals the mid."""
        r = reservation_price(mid=100.0, inventory=50, risk_aversion=0.5,
                              volatility=0.1, time_remaining=0.0)
        assert r == pytest.approx(100.0)


class TestOptimalSpread:
    def test_positive(self):
        """Spread is always positive for valid parameters."""
        delta = optimal_spread(risk_aversion=0.1, volatility=0.02,
                               time_remaining=1.0, intensity=1.5)
        assert delta > 0

    def test_formula_exact(self):
        """Verify exact formula: δ = γσ²(T-t) + (2/γ)ln(1+γ/κ)."""
        gamma, sigma, tau, kappa = 0.1, 0.02, 1.0, 1.5
        expected = gamma * sigma**2 * tau + (2 / gamma) * math.log(1 + gamma / kappa)
        delta = optimal_spread(gamma, sigma, tau, kappa)
        assert delta == pytest.approx(expected)

    def test_higher_risk_aversion_wider_spread_high_vol(self):
        """Higher risk aversion produces wider spreads when vol is high
        (the inventory-risk term γσ² dominates the adverse-selection term)."""
        d_low = optimal_spread(0.05, 5.0, 1.0, 1.5)
        d_high = optimal_spread(0.5, 5.0, 1.0, 1.5)
        assert d_high > d_low

    def test_higher_volatility_wider_spread(self):
        d_low = optimal_spread(0.1, 0.01, 1.0, 1.5)
        d_high = optimal_spread(0.1, 0.05, 1.0, 1.5)
        assert d_high > d_low

    def test_invalid_params_raise(self):
        with pytest.raises(ValueError):
            optimal_spread(risk_aversion=0, volatility=0.02,
                           time_remaining=1.0, intensity=1.5)
        with pytest.raises(ValueError):
            optimal_spread(risk_aversion=0.1, volatility=0.02,
                           time_remaining=1.0, intensity=0)


class TestComputeQuotes:
    def test_bid_below_ask(self):
        params = AvellanedaStoikovParams()
        q = compute_quotes(mid_price=100.0, inventory=0, params=params,
                           time_remaining=1.0, volatility=0.02)
        assert q["bid"] < q["ask"]

    def test_spread_bps_clamped(self):
        params = AvellanedaStoikovParams(min_spread_bps=50, max_spread_bps=500)
        q = compute_quotes(mid_price=100.0, inventory=0, params=params,
                           time_remaining=1.0, volatility=0.02)
        assert q["spread_bps"] >= 50
        assert q["spread_bps"] <= 500

    def test_inventory_skews_quotes(self):
        """Long inventory should push bid lower than the zero-inventory case."""
        params = AvellanedaStoikovParams()
        q_neutral = compute_quotes(100.0, 0, params, 1.0, 0.02)
        q_long = compute_quotes(100.0, 20, params, 1.0, 0.02)
        assert q_long["reservation_price"] < q_neutral["reservation_price"]
        assert q_long["bid"] < q_neutral["bid"]


# ---------------------------------------------------------------------------
# Volatility estimator
# ---------------------------------------------------------------------------

class TestVolatilityEstimator:
    def test_default_when_empty(self):
        est = VolatilityEstimator(window=10)
        assert est.estimate() == pytest.approx(0.02)

    def test_constant_prices_zero_vol(self):
        est = VolatilityEstimator(window=10)
        for _ in range(20):
            est.update(100.0)
        assert est.estimate() == pytest.approx(0.0)

    def test_volatile_prices(self):
        est = VolatilityEstimator(window=50)
        np.random.seed(1)
        price = 100.0
        for _ in range(60):
            price *= np.exp(np.random.normal(0, 0.02))
            est.update(price)
        vol = est.estimate()
        assert 0.005 < vol < 0.1

    def test_ready_flag(self):
        est = VolatilityEstimator(window=5)
        assert not est.ready
        est.update(100.0)
        assert not est.ready
        est.update(101.0)
        assert est.ready

    def test_window_truncation(self):
        est = VolatilityEstimator(window=5)
        for i in range(20):
            est.update(100.0 + i * 0.1)
        # internal buffer is at most window+1
        assert len(est._prices) <= 6


# ---------------------------------------------------------------------------
# Agent integration
# ---------------------------------------------------------------------------

class TestAvellanedaStoikovAgent:
    @staticmethod
    def _make_agent(**overrides) -> AvellanedaStoikovAgent:
        config = {
            "initial_quote": 10000,
            "initial_base": 0,
            "risk_aversion": 0.1,
            "volatility_window": 20,
            "horizon": 1.0,
            "order_arrival_intensity": 1.5,
            "position_limit": 50,
            "order_size_pct": 0.1,
            "max_exposure": 50,
            "total_ticks": 100,
        }
        config.update(overrides)
        return AvellanedaStoikovAgent("test-as", config)

    def test_generates_bid_on_first_tick(self):
        agent = self._make_agent()
        orders = agent.tick({"oracle_price": 100.0})
        bids = [o for o in orders if o.side == Side.BID]
        assert len(bids) == 1
        assert bids[0].price < 100.0

    def test_no_orders_without_price(self):
        agent = self._make_agent()
        orders = agent.tick({})
        assert orders == []

    def test_inventory_tracking(self):
        agent = self._make_agent()
        agent.tick({"oracle_price": 100.0})
        fill = Fill(side=Side.BID, price=99.0, size=5.0, fee=0.5)
        agent.on_fill(fill)
        assert agent.position.base_balance == 5.0

    def test_position_limit_blocks_bids(self):
        agent = self._make_agent(position_limit=5, initial_base=5.0)
        orders = agent.tick({"oracle_price": 100.0})
        bids = [o for o in orders if o.side == Side.BID]
        # inventory already at limit → no bids
        assert len(bids) == 0

    def test_time_remaining_decreases(self):
        agent = self._make_agent(total_ticks=10)
        assert agent.time_remaining == pytest.approx(1.0)
        agent.tick({"oracle_price": 100.0})
        assert agent.time_remaining == pytest.approx(0.9)
        for _ in range(9):
            agent.tick({"oracle_price": 100.0})
        assert agent.time_remaining == pytest.approx(0.0, abs=1e-9)

    def test_pnl_after_roundtrip(self):
        agent = self._make_agent()
        agent.tick({"oracle_price": 100.0})

        # Buy
        agent.on_fill(Fill(side=Side.BID, price=99.0, size=10.0, fee=1.0))
        # Sell higher
        agent.on_fill(Fill(side=Side.ASK, price=101.0, size=10.0, fee=1.0))

        pnl = agent.get_pnl(100.0)
        # realized = 10*(101-99) - 1.0 = 19.0 (sell fee only in realized calc)
        assert pnl["realized"] == pytest.approx(19.0)

    def test_rebalance_when_over_limit(self):
        agent = self._make_agent(position_limit=5)
        agent.position.base_balance = 10.0
        agent.last_mid = 100.0
        orders = agent.rebalance()
        assert len(orders) == 1
        assert orders[0].side == Side.ASK
        assert orders[0].size == pytest.approx(5.0)

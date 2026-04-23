"""Tests for ML-powered pricing agent."""

import pytest
from src.agents.ml_pricing_agent import MLPricingAgent, OnlineLinearModel


class TestOnlineLinearModel:
    def test_initial_prediction_is_zero(self):
        model = OnlineLinearModel(n_features=3)
        assert model.predict([1.0, 2.0, 3.0]) == 0.0

    def test_learns_simple_pattern(self):
        model = OnlineLinearModel(n_features=1)
        # Train: y = 2x
        for _ in range(50):
            model.update([1.0], 2.0)
        assert abs(model.predict([1.0]) - 2.0) < 0.1

    def test_confidence_increases_with_samples(self):
        model = OnlineLinearModel(n_features=2)
        assert model.confidence == 0.0
        for i in range(100):
            model.update([float(i), float(i * 2)], float(i * 3))
        assert model.confidence == 1.0

    def test_weight_norm(self):
        model = OnlineLinearModel(n_features=2)
        assert model.weight_norm == 0.0
        model.update([1.0, 0.0], 5.0)
        assert model.weight_norm > 0


class TestMLPricingAgent:
    def _make_agent(self, **config_overrides):
        config = {
            "initial_base": 0.0,
            "initial_quote": 10000.0,
            "max_exposure": 10.0,
            "base_spread_bps": 10.0,
            "volatility_mult": 2.0,
            "ml_skew_weight": 0.5,
            "inventory_skew_bps": 5.0,
            "order_size": 1.0,
            "warmup_ticks": 5,
            "ema_fast": 3,
            "ema_slow": 10,
        }
        config.update(config_overrides)
        return MLPricingAgent("test-ml", config)

    def test_initial_tick_produces_orders(self):
        agent = self._make_agent(warmup_ticks=0)
        # Feed a few prices first to build history
        for p in [100.0, 100.1, 100.05]:
            agent.tick({"mid_price": p})
        orders = agent.tick({"mid_price": 100.0})
        assert len(orders) == 2
        bids = [o for o in orders if o.side.value == "bid"]
        asks = [o for o in orders if o.side.value == "ask"]
        assert len(bids) == 1
        assert len(asks) == 1
        assert bids[0].price < asks[0].price

    def test_warmup_period_no_ml_influence(self):
        agent = self._make_agent(warmup_ticks=10)
        for i in range(5):
            agent.tick({"mid_price": 100.0 + i * 0.1})
        # During warmup, ML confidence should be 0
        signals = agent.evaluate_market({"mid_price": 100.5})
        assert signals["ml_confidence"] == 0.0

    def test_spread_widens_with_volatility(self):
        agent = self._make_agent(warmup_ticks=0)
        # Low volatility
        for p in [100.0] * 20:
            agent.tick({"mid_price": p})
        low_vol_orders = agent.tick({"mid_price": 100.0})
        low_spread = low_vol_orders[1].price - low_vol_orders[0].price

        # High volatility
        agent2 = self._make_agent(warmup_ticks=0)
        prices = [100.0, 101.0, 99.0, 102.0, 98.0] * 4
        for p in prices:
            agent2.tick({"mid_price": p})
        high_vol_orders = agent2.tick({"mid_price": 100.0})
        high_spread = high_vol_orders[1].price - high_vol_orders[0].price

        assert high_spread > low_spread

    def test_inventory_skew_direction(self):
        agent = self._make_agent(warmup_ticks=0, inventory_skew_bps=50)
        # Build price history
        for p in [100.0] * 10:
            agent.tick({"mid_price": p})

        # Simulate long inventory
        agent.position.base_balance = 5.0
        orders_long = agent.tick({"mid_price": 100.0})
        bid_long = orders_long[0].price
        ask_long = orders_long[1].price
        mid_long = (bid_long + ask_long) / 2

        # Simulate short inventory
        agent2 = self._make_agent(warmup_ticks=0, inventory_skew_bps=50)
        for p in [100.0] * 10:
            agent2.tick({"mid_price": p})
        agent2.position.base_balance = -5.0
        orders_short = agent2.tick({"mid_price": 100.0})
        mid_short = (orders_short[0].price + orders_short[1].price) / 2

        # Long inventory → lower mid (encourage selling)
        assert mid_long < mid_short

    def test_rebalance_when_long(self):
        agent = self._make_agent()
        for p in [100.0] * 5:
            agent.tick({"mid_price": p})
        agent.position.base_balance = 15.0  # Over max_exposure=10
        rebalance_orders = agent.rebalance()
        assert len(rebalance_orders) == 1
        assert rebalance_orders[0].side.value == "ask"

    def test_rebalance_when_short(self):
        agent = self._make_agent()
        for p in [100.0] * 5:
            agent.tick({"mid_price": p})
        agent.position.base_balance = -15.0
        rebalance_orders = agent.rebalance()
        assert len(rebalance_orders) == 1
        assert rebalance_orders[0].side.value == "bid"

    def test_model_diagnostics(self):
        agent = self._make_agent(warmup_ticks=0)
        for p in [100.0 + i * 0.01 for i in range(20)]:
            agent.tick({"mid_price": p})
        diag = agent.get_model_diagnostics()
        assert diag["n_samples"] > 0
        assert diag["tick_count"] == 20
        assert len(diag["weights"]) == 5

    def test_bid_always_less_than_ask(self):
        agent = self._make_agent(warmup_ticks=0)
        prices = [100 + (i % 10) * 0.5 for i in range(50)]
        for p in prices:
            orders = agent.tick({"mid_price": p})
            if len(orders) == 2:
                assert orders[0].price < orders[1].price, f"bid >= ask at price {p}"

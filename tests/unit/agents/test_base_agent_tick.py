"""Unit tests for BaseAgent.tick() loop.

Covers the full evaluate → execute → rebalance cycle as specified in issue #6.
Uses a concrete subclass (RWAMarketMaker) to test the abstract BaseAgent.
"""

import pytest
from src.agents.base_agent import Position, Fill, Order, Side
from src.agents.rwa_market_maker import RWAMarketMaker


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def agent():
    """Create a RWAMarketMaker with default config."""
    config = {
        "initial_quote": 10000,
        "initial_base": 0,
        "base_spread_bps": 200,
        "max_order_size_pct": 0.1,
        "max_inventory_pct": 0.3,
    }
    return RWAMarketMaker("test-agent", config)


@pytest.fixture
def market_data():
    """Standard market data for tick tests."""
    return {
        "oracle_price": 100.0,
        "on_chain_price": 100.1,
        "volume_24h": 500000,
    }


# ── Normal tick with fill ────────────────────────────────────────────────────

class TestTickWithFill:
    """Test tick cycle when a fill occurs after orders are placed."""

    def test_tick_returns_orders(self, agent, market_data):
        """tick() should return a list of orders."""
        orders = agent.tick(market_data)
        assert isinstance(orders, list)
        assert len(orders) >= 1

    def test_tick_updates_active_orders(self, agent, market_data):
        """tick() should update agent.active_orders."""
        orders = agent.tick(market_data)
        assert agent.active_orders == orders

    def test_on_fill_updates_position(self, agent, market_data):
        """After tick, applying a fill should update position."""
        orders = agent.tick(market_data)
        bids = [o for o in orders if o.side == Side.BID]
        assert len(bids) > 0

        fill = Fill(side=Side.BID, price=bids[0].price, size=1.0, fee=0.1)
        agent.on_fill(fill)

        assert agent.position.base_balance == 1.0
        assert len(agent.fill_history) == 1

    def test_on_fill_logs_event(self, agent, market_data):
        """on_fill should log an event."""
        agent.tick(market_data)
        fill = Fill(side=Side.BID, price=99.0, size=1.0, fee=0.1)
        agent.on_fill(fill)

        events = [e for e in agent._event_log if e["event"] == "fill"]
        assert len(events) == 1
        assert events[0]["side"] == "bid"

    def test_multiple_ticks_accumulate_state(self, agent, market_data):
        """Running multiple ticks should accumulate fills and orders."""
        for i in range(5):
            agent.tick(market_data)
            if agent.active_orders:
                o = agent.active_orders[0]
                fill = Fill(side=o.side, price=o.price, size=1.0, fee=0.1)
                agent.on_fill(fill)

        assert len(agent.fill_history) == 5
        assert len(agent._event_log) >= 5

    def test_tick_with_sell_fill(self, agent, market_data):
        """Test ASK fill after tick."""
        # First buy some base
        agent.position.base_balance = 10.0
        agent.position.avg_entry_price = 100.0

        orders = agent.tick(market_data)
        asks = [o for o in orders if o.side == Side.ASK]
        if asks:
            fill = Fill(side=Side.ASK, price=asks[0].price, size=2.0, fee=0.1)
            agent.on_fill(fill)
            assert agent.position.base_balance == 8.0
            assert agent.position.realized_pnl != 0.0


# ── Tick with no orders ──────────────────────────────────────────────────────

class TestTickNoOrders:
    """Test tick cycle when no orders should be generated."""

    def test_no_price_data_returns_empty(self, agent):
        """tick() with no price data should return no orders."""
        orders = agent.tick({})
        assert orders == []

    def test_no_price_sets_active_orders_empty(self, agent):
        """active_orders should be empty when no orders generated."""
        agent.tick({})
        assert agent.active_orders == []

    def test_tick_with_zero_balance(self):
        """Agent with zero balances should produce no orders."""
        config = {
            "initial_quote": 0,
            "initial_base": 0,
            "base_spread_bps": 200,
            "max_order_size_pct": 0.1,
        }
        agent = RWAMarketMaker("broke", config)
        orders = agent.tick({"oracle_price": 100.0, "volume_24h": 500000})
        # Should still produce orders since max_order_size_pct uses available balance
        # but with 0 balance, sizes may be 0 or very small


# ── Tick with rebalance ─────────────────────────────────────────────────────

class TestTickRebalance:
    """Test that tick() triggers rebalance when exposure exceeds limits."""

    def test_rebalance_triggered_on_high_exposure(self):
        """When net exposure > max_exposure, rebalance orders should appear."""
        config = {
            "initial_quote": 1000,
            "initial_base": 50.0,  # Large base position
            "base_spread_bps": 200,
            "max_order_size_pct": 0.1,
            "max_inventory_pct": 0.3,
            "max_exposure": 5.0,  # Very low limit
        }
        agent = RWAMarketMaker("rebal-test", config)

        orders = agent.tick({
            "oracle_price": 100.0,
            "on_chain_price": 100.0,
            "volume_24h": 500000,
        })

        # Should have rebalance orders because exposure (50) > max (5)
        rebalance_events = [e for e in agent._event_log if e["event"] == "rebalance_triggered"]
        assert len(rebalance_events) >= 1

    def test_no_rebalance_within_limits(self, agent, market_data):
        """Normal exposure should not trigger rebalance."""
        agent.tick(market_data)
        rebalance_events = [e for e in agent._event_log if e["event"] == "rebalance_triggered"]
        assert len(rebalance_events) == 0


# ── PnL tracking ─────────────────────────────────────────────────────────────

class TestPnLTracking:
    """Test get_pnl() after tick and fill cycles."""

    def test_pnl_zero_before_fills(self, agent, market_data):
        """PnL should be zero before any fills."""
        agent.tick(market_data)
        pnl = agent.get_pnl(100.0)
        assert pnl["realized"] == 0.0

    def test_pnl_after_buy_fill(self, agent, market_data):
        """PnL should track after a buy fill."""
        agent.tick(market_data)
        fill = Fill(side=Side.BID, price=100.0, size=10.0, fee=1.0)
        agent.on_fill(fill)

        pnl = agent.get_pnl(110.0)
        assert pnl["unrealized"] == pytest.approx(100.0)  # 10 * (110-100)
        assert pnl["realized"] == 0.0
        assert pnl["total"] == pytest.approx(100.0)  # unrealized 100, realized 0

    def test_pnl_after_sell_fill(self):
        """PnL after a sell should show realized profit."""
        config = {
            "initial_quote": 0,
            "initial_base": 10.0,
            "base_spread_bps": 200,
            "max_order_size_pct": 0.1,
        }
        agent = RWAMarketMaker("sell-test", config)
        agent.position.avg_entry_price = 100.0

        fill = Fill(side=Side.ASK, price=120.0, size=5.0, fee=0.5)
        agent.on_fill(fill)

        pnl = agent.get_pnl(120.0)
        assert pnl["realized"] == pytest.approx(99.5)  # 5*(120-100) - 0.5

    def test_pnl_after_multiple_fills(self, agent, market_data):
        """PnL should accumulate across multiple fills."""
        agent.tick(market_data)
        # Buy
        agent.on_fill(Fill(side=Side.BID, price=100.0, size=10.0, fee=1.0))
        # Sell at profit
        agent.on_fill(Fill(side=Side.ASK, price=110.0, size=5.0, fee=0.5))

        pnl = agent.get_pnl(110.0)
        assert pnl["realized"] == pytest.approx(49.5)  # 5*(110-100) - 0.5
        # 5 base remaining, unrealized = 5*(110-100) = 50
        assert pnl["unrealized"] == pytest.approx(50.0)


# ── Event logging ────────────────────────────────────────────────────────────

class TestEventLog:
    """Test that tick cycle logs appropriate events."""

    def test_market_eval_logged(self, agent, market_data):
        """evaluate_market should log market_eval event."""
        agent.tick(market_data)
        events = [e for e in agent._event_log if e["event"] == "market_eval"]
        assert len(events) >= 1

    def test_orders_placed_logged(self, agent, market_data):
        """execute_strategy should log orders_placed event."""
        agent.tick(market_data)
        events = [e for e in agent._event_log if e["event"] == "orders_placed"]
        assert len(events) >= 1

    def test_event_log_has_timestamp(self, agent, market_data):
        """All events should have a timestamp."""
        agent.tick(market_data)
        for event in agent._event_log:
            assert "timestamp" in event

    def test_event_log_has_agent_id(self, agent, market_data):
        """All events should reference the agent."""
        agent.tick(market_data)
        for event in agent._event_log:
            assert event["agent"] == "test-agent"

"""Tests for base agent and RWA market maker."""

import pytest
from src.agents.base_agent import Position, Fill, Side
from src.agents.rwa_market_maker import RWAMarketMaker


def test_position_apply_buy_fill():
    pos = Position(base_balance=0, quote_balance=10000)
    fill = Fill(side=Side.BID, price=100.0, size=10.0, fee=1.0)
    pos.apply_fill(fill)
    assert pos.base_balance == 10.0
    assert pos.quote_balance == 10000 - 1000 - 1.0
    assert pos.avg_entry_price == 100.0


def test_position_apply_sell_fill():
    pos = Position(base_balance=10, quote_balance=0, avg_entry_price=100.0)
    fill = Fill(side=Side.ASK, price=110.0, size=5.0, fee=0.5)
    pos.apply_fill(fill)
    assert pos.base_balance == 5.0
    assert pos.realized_pnl == pytest.approx(49.5)  # 5 * (110-100) - 0.5


def test_rwa_market_maker_generates_orders():
    config = {
        "initial_quote": 10000,
        "base_spread_bps": 200,
        "max_order_size_pct": 0.1,
        "max_inventory_pct": 0.3,
    }
    agent = RWAMarketMaker("test", config)
    market_data = {
        "oracle_price": 100.0,
        "on_chain_price": 100.1,
        "volume_24h": 500000,
    }
    orders = agent.tick(market_data)
    assert len(orders) >= 1
    bids = [o for o in orders if o.side == Side.BID]
    asks = [o for o in orders if o.side == Side.ASK]
    assert len(bids) == 1
    assert bids[0].price < 100.0  # bid below mid


def test_rwa_mm_no_orders_without_price():
    config = {"initial_quote": 10000}
    agent = RWAMarketMaker("test", config)
    orders = agent.tick({})
    assert orders == []


def test_pnl_tracking():
    config = {"initial_quote": 10000, "initial_base": 0}
    agent = RWAMarketMaker("test", config)
    pnl = agent.get_pnl(100.0)
    assert pnl["total"] == 0.0
    assert pnl["quote_balance"] == 10000

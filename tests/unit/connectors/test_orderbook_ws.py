"""Tests for order book WebSocket connector."""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.connectors.orderbook_ws import OrderBookWebSocket, OrderBookLevel, OrderBookSnapshot


class TestOrderBookState:
    def test_initial_state(self):
        ob = OrderBookWebSocket("ws://test", "ETH/USD")
        assert not ob.is_connected
        snap = ob.snapshot
        assert len(snap.bids) == 0
        assert len(snap.asks) == 0

    def test_apply_bids(self):
        ob = OrderBookWebSocket("ws://test", "ETH/USD")
        data = {
            "bids": [["101.0", "1.5"], ["100.5", "2.0"]],
            "asks": [["102.0", "0.5"]],
        }
        ob._apply(data)
        snap = ob.snapshot
        assert len(snap.bids) == 2
        assert snap.bids[0].price == 101.0
        assert snap.bids[0].size == 1.5
        assert snap.asks[0].price == 102.0

    def test_apply_remove_level(self):
        ob = OrderBookWebSocket("ws://test", "ETH/USD")
        ob._bids[100.0] = 5.0
        data = {"bids": [["100.0", "0"]], "asks": []}
        ob._apply(data)
        assert 100.0 not in ob._bids

    def test_apply_short_keys(self):
        ob = OrderBookWebSocket("ws://test", "ETH/USD")
        data = {"b": [["2000.0", "10.0"]], "a": [["2005.0", "5.0"]]}
        ob._apply(data)
        snap = ob.snapshot
        assert snap.bids[0].price == 2000.0
        assert snap.asks[0].price == 2005.0

    def test_snapshot_max_depth(self):
        ob = OrderBookWebSocket("ws://test", "ETH/USD", max_depth=3)
        bids = [[str(float(i)), "1.0"] for i in range(10, 0, -1)]
        ob._apply({"bids": bids, "asks": [["9999.0", "1.0"]]})
        snap = ob.snapshot
        assert len(snap.bids) == 3
        assert snap.bids[0].price == 10.0

    def test_callbacks_invoked(self):
        ob = OrderBookWebSocket("ws://test", "ETH/USD")
        received = []
        ob.on_update(lambda s: received.append(s))
        ob._apply({"bids": [["100.0", "1.0"]], "asks": []})
        assert len(received) == 1
        assert received[0].bids[0].price == 100.0

    def test_callback_exception_does_not_break(self):
        ob = OrderBookWebSocket("ws://test", "ETH/USD")
        good = []
        def bad(_):
            raise ValueError("oops")
        ob.on_update(bad)
        ob.on_update(lambda s: good.append(s))
        ob._apply({"bids": [["50.0", "1.0"]], "asks": []})
        assert len(good) == 1


class TestOrderBookHandle:
    @pytest.mark.asyncio
    async def test_handle_valid_message(self):
        ob = OrderBookWebSocket("ws://test", "ETH/USD")
        msg = json.dumps({"bids": [["100.0", "1.0"]], "asks": []})
        await ob._handle(msg)
        assert ob._bids.get(100.0) == 1.0

    @pytest.mark.asyncio
    async def test_handle_invalid_json(self):
        ob = OrderBookWebSocket("ws://test", "ETH/USD")
        await ob._handle("not json {{")  # should not crash

    @pytest.mark.asyncio
    async def test_disconnect(self):
        ob = OrderBookWebSocket("ws://test", "ETH/USD")
        ob._running = True
        await ob.disconnect()
        assert not ob._running

"""Tests for WebSocket order book feed."""

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.connectors.orderbook_feed import (
    BinanceOrderBookFeed,
    OrderBookLevel,
    OrderBookSnapshot,
    OrderSide,
    UniswapV3OrderBookFeed,
    WebSocketOrderBookConfig,
    WebSocketOrderBookFeed,
    create_binance_feed,
)


# --- OrderBookSnapshot tests ---


class TestOrderBookSnapshot:
    def test_empty_snapshot(self):
        ob = OrderBookSnapshot(symbol="btcusdt")
        assert ob.best_bid is None
        assert ob.best_ask is None
        assert ob.spread is None
        assert ob.mid_price is None
        assert ob.bids == []
        assert ob.asks == []

    def test_best_bid_ask(self):
        ob = OrderBookSnapshot(
            symbol="btcusdt",
            bids=[
                OrderBookLevel(price=50000.0, quantity=1.0, side=OrderSide.BID),
                OrderBookLevel(price=49900.0, quantity=2.0, side=OrderSide.BID),
            ],
            asks=[
                OrderBookLevel(price=50100.0, quantity=1.5, side=OrderSide.ASK),
                OrderBookLevel(price=50200.0, quantity=1.0, side=OrderSide.ASK),
            ],
        )
        assert ob.best_bid == 50000.0
        assert ob.best_ask == 50100.0
        assert ob.spread == pytest.approx(100.0)
        assert ob.mid_price == pytest.approx(50050.0)

    def test_depth_at_price(self):
        ob = OrderBookSnapshot(
            symbol="btcusdt",
            bids=[
                OrderBookLevel(price=50000.0, quantity=1.0, side=OrderSide.BID),
                OrderBookLevel(price=49999.0, quantity=0.5, side=OrderSide.BID),
                OrderBookLevel(price=49000.0, quantity=3.0, side=OrderSide.BID),
            ],
            asks=[],
        )
        # Within 0.1% of 50000
        depth = ob.depth_at_price(50000.0, OrderSide.BID, tolerance=0.001)
        assert depth == pytest.approx(1.5)  # 1.0 + 0.5


# --- WebSocketOrderBookFeed tests ---


class TestWebSocketOrderBookFeed:
    def _make_feed(self):
        config = WebSocketOrderBookConfig(
            url="wss://test.example.com/ws",
            max_reconnects=3,
            reconnect_base_delay=0.01,
        )
        feed = WebSocketOrderBookFeed(config)
        # Make it concrete for testing by implementing abstract methods
        feed._send_subscribe = AsyncMock()
        feed._parse_message = MagicMock(return_value=None)
        return feed, config

    def test_subscribe_adds_to_set(self):
        feed, config = self._make_feed()
        asyncio.get_event_loop().run_until_complete(feed.subscribe("btcusdt"))
        assert "btcusdt" in feed._subscriptions

    def test_subscribe_normalizes_symbol(self):
        feed, config = self._make_feed()
        asyncio.get_event_loop().run_until_complete(feed.subscribe("BTC/USDT"))
        assert "btcusdt" in feed._subscriptions

    def test_unsubscribe_removes_from_set(self):
        feed, config = self._make_feed()
        asyncio.get_event_loop().run_until_complete(feed.subscribe("btcusdt"))
        asyncio.get_event_loop().run_until_complete(feed.unsubscribe("btcusdt"))
        assert "btcusdt" not in feed._subscriptions

    def test_get_orderbook_returns_none_for_unknown(self):
        feed, config = self._make_feed()
        assert feed.get_orderbook("btcusdt") is None

    def test_connected_symbols(self):
        feed, config = self._make_feed()
        asyncio.get_event_loop().run_until_complete(feed.subscribe("btcusdt"))
        asyncio.get_event_loop().run_until_complete(feed.subscribe("ethusdt"))
        assert feed.connected_symbols == {"btcusdt", "ethusdt"}

    def test_is_connected_initially_false(self):
        feed, config = self._make_feed()
        assert not feed.is_connected

    def test_callbacks(self):
        feed, config = self._make_feed()
        updates = []
        feed.on_update(lambda sym, ob: updates.append((sym, ob)))

        snapshot = OrderBookSnapshot(symbol="btcusdt")
        feed._notify_callbacks("btcusdt", snapshot)
        assert len(updates) == 1
        assert updates[0][0] == "btcusdt"

    def test_callback_error_doesnt_crash(self):
        feed, config = self._make_feed()
        feed.on_update(lambda sym, ob: 1 / 0)
        # Should not raise
        feed._notify_callbacks("btcusdt", OrderBookSnapshot(symbol="btcusdt"))

    def test_apply_full_snapshot(self):
        feed, config = self._make_feed()
        snapshot = OrderBookSnapshot(
            symbol="btcusdt",
            bids=[OrderBookLevel(price=50000.0, quantity=1.0, side=OrderSide.BID)],
            asks=[OrderBookLevel(price=50100.0, quantity=1.5, side=OrderSide.ASK)],
            is_delta=False,
        )
        feed._apply_update(snapshot)
        ob = feed.get_orderbook("btcusdt")
        assert ob is not None
        assert ob.best_bid == 50000.0
        assert ob.best_ask == 50100.0

    def test_delta_queued_before_snapshot(self):
        feed, config = self._make_feed()
        delta = OrderBookSnapshot(
            symbol="btcusdt",
            bids=[OrderBookLevel(price=50000.0, quantity=1.0, side=OrderSide.BID)],
            is_delta=True,
            sequence=2,
        )
        feed._apply_update(delta)
        # Delta should be queued, not applied
        assert feed.get_orderbook("btcusdt") is None
        assert "btcusdt" in feed._pending_deltas

    def test_delta_applied_after_snapshot(self):
        feed, config = self._make_feed()
        # First: snapshot
        snapshot = OrderBookSnapshot(
            symbol="btcusdt",
            bids=[OrderBookLevel(price=50000.0, quantity=1.0, side=OrderSide.BID)],
            asks=[OrderBookLevel(price=50100.0, quantity=1.5, side=OrderSide.ASK)],
            is_delta=False,
            sequence=1,
        )
        feed._apply_update(snapshot)

        # Then: delta that updates bid quantity
        delta = OrderBookSnapshot(
            symbol="btcusdt",
            bids=[OrderBookLevel(price=50000.0, quantity=2.0, side=OrderSide.BID)],
            is_delta=True,
            sequence=2,
        )
        feed._apply_update(delta)

        ob = feed.get_orderbook("btcusdt")
        assert ob is not None
        assert ob.bids[0].quantity == 2.0
        assert ob.sequence == 2

    def test_delta_removes_zero_quantity_level(self):
        feed, config = self._make_feed()
        snapshot = OrderBookSnapshot(
            symbol="btcusdt",
            bids=[
                OrderBookLevel(price=50000.0, quantity=1.0, side=OrderSide.BID),
                OrderBookLevel(price=49900.0, quantity=2.0, side=OrderSide.BID),
            ],
            is_delta=False,
            sequence=1,
        )
        feed._apply_update(snapshot)

        # Delta removes the 50000 bid
        delta = OrderBookSnapshot(
            symbol="btcusdt",
            bids=[OrderBookLevel(price=50000.0, quantity=0.0, side=OrderSide.BID)],
            is_delta=True,
            sequence=2,
        )
        feed._apply_update(delta)

        ob = feed.get_orderbook("btcusdt")
        assert len(ob.bids) == 1
        assert ob.bids[0].price == 49900.0

    def test_update_level_static(self):
        levels = [
            OrderBookLevel(price=100.0, quantity=5.0, side=OrderSide.BID),
            OrderBookLevel(price=99.0, quantity=3.0, side=OrderSide.BID),
        ]
        # Update existing level
        WebSocketOrderBookFeed._update_level(
            levels, OrderBookLevel(price=100.0, quantity=8.0, side=OrderSide.BID)
        )
        assert levels[0].quantity == 8.0
        assert len(levels) == 2

        # Add new level
        WebSocketOrderBookFeed._update_level(
            levels, OrderBookLevel(price=101.0, quantity=2.0, side=OrderSide.BID)
        )
        assert len(levels) == 3

        # Remove level with zero quantity
        WebSocketOrderBookFeed._update_level(
            levels, OrderBookLevel(price=99.0, quantity=0.0, side=OrderSide.BID)
        )
        assert len(levels) == 2
        assert all(l.price != 99.0 for l in levels)


# --- BinanceOrderBookFeed tests ---


class TestBinanceOrderBookFeed:
    def _make_feed(self, depth=20):
        config = WebSocketOrderBookConfig(
            url="wss://stream.binance.com:9443/ws",
            max_reconnects=3,
        )
        return BinanceOrderBookFeed(config, depth=depth)

    def test_invalid_depth_raises(self):
        config = WebSocketOrderBookConfig(url="wss://test.com")
        with pytest.raises(ValueError, match="depth must be one of"):
            BinanceOrderBookFeed(config, depth=50)

    def test_parse_binance_depth_update(self):
        feed = self._make_feed()
        message = {
            "e": "depthUpdate",
            "E": 1672515782136,
            "s": "BTCUSDT",
            "U": 157,
            "u": 160,
            "b": [
                ["50000.00", "1.000"],
                ["49900.00", "2.500"],
            ],
            "a": [
                ["50100.00", "1.500"],
                ["50200.00", "0.800"],
            ],
        }
        snapshot = feed._parse_message(message)
        assert snapshot is not None
        assert snapshot.symbol == "btcusdt"
        assert len(snapshot.bids) == 2
        assert len(snapshot.asks) == 2
        assert snapshot.bids[0].price == 50000.0
        assert snapshot.bids[0].quantity == 1.0
        assert snapshot.asks[0].price == 50100.0
        assert snapshot.is_delta is True
        assert snapshot.sequence == 160

    def test_parse_ignores_non_depth_events(self):
        feed = self._make_feed()
        message = {"e": "trade", "s": "BTCUSDT", "p": "50000.00"}
        assert feed._parse_message(message) is None

    def test_parse_ignores_empty_symbol(self):
        feed = self._make_feed()
        message = {"e": "depthUpdate", "s": ""}
        assert feed._parse_message(message) is None


# --- UniswapV3OrderBookFeed tests ---


class TestUniswapV3OrderBookFeed:
    def test_subscribe(self):
        feed = UniswapV3OrderBookFeed(
            pool_address="0x1234",
            w3_provider_url="https://eth.llamarpc.com",
        )
        asyncio.get_event_loop().run_until_complete(feed.subscribe("eth/usdc"))
        assert feed.get_orderbook("eth/usdc") is not None

    def test_update_from_slot0(self):
        feed = UniswapV3OrderBookFeed(
            pool_address="0x1234",
            w3_provider_url="https://eth.llamarpc.com",
        )
        asyncio.get_event_loop().run_until_complete(feed.subscribe("eth/usdc"))

        # Use realistic sqrtPriceX96 for ETH ~$2000
        # price = (sqrtPriceX96 / 2^96)^2 ≈ 2000
        import math
        sqrt_price = int(math.sqrt(2000) * (2 ** 96))
        liquidity = 10 ** 23  # 100 ETH worth of liquidity

        feed.update_from_slot0(
            symbol="eth/usdc",
            sqrt_price_x96=sqrt_price,
            liquidity=liquidity,
            tick=0,
            fee=3000,
        )

        ob = feed.get_orderbook("eth/usdc")
        assert ob is not None
        assert len(ob.bids) == 10
        assert len(ob.asks) == 10
        assert ob.bids[0].price > ob.bids[-1].price  # Sorted descending
        assert ob.asks[0].price < ob.asks[-1].price   # Sorted ascending
        assert ob.best_bid > 0
        assert ob.best_ask > ob.best_bid  # Bids < asks
        assert ob.spread > 0

    def test_unsubscribe(self):
        feed = UniswapV3OrderBookFeed(
            pool_address="0x1234",
            w3_provider_url="https://eth.llamarpc.com",
        )
        asyncio.get_event_loop().run_until_complete(feed.subscribe("eth/usdc"))
        asyncio.get_event_loop().run_until_complete(feed.unsubscribe("eth/usdc"))
        assert feed.get_orderbook("eth/usdc") is None

    def test_sequence_increments(self):
        feed = UniswapV3OrderBookFeed(
            pool_address="0x1234",
            w3_provider_url="https://eth.llamarpc.com",
        )
        asyncio.get_event_loop().run_until_complete(feed.subscribe("eth/usdc"))

        import math
        sqrt_price = int(math.sqrt(2000) * (2 ** 96))
        feed.update_from_slot0("eth/usdc", sqrt_price, 10**23, 0, 3000)
        feed.update_from_slot0("eth/usdc", sqrt_price, 10**23, 0, 3000)

        ob = feed.get_orderbook("eth/usdc")
        assert ob.sequence == 2


# --- create_binance_feed factory tests ---


class TestCreateBinanceFeed:
    def test_creates_feed_and_config(self):
        feed, config = create_binance_feed(["btcusdt", "ethusdt"], depth=10)
        assert isinstance(feed, BinanceOrderBookFeed)
        assert isinstance(config, WebSocketOrderBookConfig)
        assert config.url == "wss://stream.binance.com:9443/ws"
        assert feed.depth == 10

    def test_default_depth(self):
        feed, _ = create_binance_feed(["btcusdt"])
        assert feed.depth == 20


# --- WebSocketOrderBookConfig tests ---


class TestWebSocketOrderBookConfig:
    def test_defaults(self):
        config = WebSocketOrderBookConfig(url="wss://test.com")
        assert config.ping_interval == 20.0
        assert config.reconnect_base_delay == 1.0
        assert config.reconnect_max_delay == 60.0
        assert config.max_reconnects == 50
        assert config.heartbeat_timeout == 30.0

    def test_custom_values(self):
        config = WebSocketOrderBookConfig(
            url="wss://test.com",
            ping_interval=10.0,
            max_reconnects=5,
            heartbeat_timeout=15.0,
        )
        assert config.ping_interval == 10.0
        assert config.max_reconnects == 5
        assert config.heartbeat_timeout == 15.0

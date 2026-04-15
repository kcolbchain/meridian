"""Tests for WebSocket price feed connector."""

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.connectors.websocket_feed import (
    WebSocketPriceFeed,
    WebSocketConfig,
    create_uniswap_ws_config,
)
from src.oracle.price_feed import PricePoint


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def config():
    return WebSocketConfig(
        url="ws://localhost:8080",
        subscription_msg={"method": "subscribe", "params": ["price"]},
    )


@pytest.fixture
def feed(config):
    return WebSocketPriceFeed(config)


# ── Price parsing ─────────────────────────────────────────────────────────────

class TestPriceParsing:
    def test_parse_standard_format(self, feed):
        data = {"asset": "ETH/USD", "price": 3500.50, "source": "uniswap"}
        point = feed._parse_price(data)
        assert point is not None
        assert point.asset == "ETH/USD"
        assert point.price == 3500.50
        assert point.source == "uniswap"

    def test_parse_short_keys(self, feed):
        data = {"s": "BTC/USD", "p": 95000.0}
        point = feed._parse_price(data)
        assert point is not None
        assert point.asset == "BTC/USD"
        assert point.price == 95000.0

    def test_parse_missing_asset_returns_none(self, feed):
        data = {"price": 100.0}
        assert feed._parse_price(data) is None

    def test_parse_missing_price_returns_none(self, feed):
        data = {"asset": "ETH/USD"}
        assert feed._parse_price(data) is None

    def test_parse_with_confidence(self, feed):
        data = {"asset": "ETH/USD", "price": 3500.0, "confidence": 0.99}
        point = feed._parse_price(data)
        assert point.confidence == 0.99

    def test_parse_default_confidence(self, feed):
        data = {"asset": "ETH/USD", "price": 3500.0}
        point = feed._parse_price(data)
        assert point.confidence == 0.95


# ── Price cache ──────────────────────────────────────────────────────────────

class TestPriceCache:
    def test_update_price_caches_latest(self, feed):
        point = PricePoint(asset="ETH/USD", price=3500.0, currency="USD",
                          source="test", timestamp=None)
        feed._update_price(point)
        assert feed.get_price("ETH/USD") == point

    def test_update_price_appends_history(self, feed):
        for i in range(5):
            point = PricePoint(asset="ETH/USD", price=3500.0 + i,
                              currency="USD", source="test", timestamp=None)
            feed._update_price(point)
        history = feed.get_historical("ETH/USD", 3)
        assert len(history) == 3

    def test_history_truncated_to_1000(self, feed):
        for i in range(1100):
            point = PricePoint(asset="ETH/USD", price=float(i),
                              currency="USD", source="test", timestamp=None)
            feed._update_price(point)
        assert len(feed._history["ETH/USD"]) == 1000

    def test_get_price_unknown_asset_returns_none(self, feed):
        assert feed.get_price("UNKNOWN") is None


# ── Callbacks ────────────────────────────────────────────────────────────────

class TestCallbacks:
    def test_on_price_update_callback(self, feed):
        received = []
        feed.on_price_update(lambda p: received.append(p))
        point = PricePoint(asset="ETH/USD", price=3500.0, currency="USD",
                          source="test", timestamp=None)
        feed._update_price(point)
        assert len(received) == 1
        assert received[0].price == 3500.0

    def test_multiple_callbacks(self, feed):
        r1, r2 = [], []
        feed.on_price_update(lambda p: r1.append(p))
        feed.on_price_update(lambda p: r2.append(p))
        point = PricePoint(asset="BTC/USD", price=95000.0, currency="USD",
                          source="test", timestamp=None)
        feed._update_price(point)
        assert len(r1) == 1 and len(r2) == 1

    def test_callback_exception_does_not_break_others(self, feed):
        good = []
        def bad_callback(p):
            raise ValueError("oops")
        feed.on_price_update(bad_callback)
        feed.on_price_update(lambda p: good.append(p))
        point = PricePoint(asset="ETH/USD", price=1.0, currency="USD",
                          source="test", timestamp=None)
        feed._update_price(point)
        assert len(good) == 1  # Second callback still works


# ── Fallback ──────────────────────────────────────────────────────────────────

class TestFallback:
    def test_fallback_when_no_cached_price(self, config):
        mock = MagicMock()
        mock.get_price.return_value = PricePoint(
            asset="ETH/USD", price=3400.0, currency="USD",
            source="mock", timestamp=None
        )
        feed = WebSocketPriceFeed(config, fallback=mock)
        result = feed.get_price("ETH/USD")
        assert result.price == 3400.0
        mock.get_price.assert_called_once_with("ETH/USD")

    def test_cached_price_takes_priority_over_fallback(self, config):
        mock = MagicMock()
        feed = WebSocketPriceFeed(config, fallback=mock)
        point = PricePoint(asset="ETH/USD", price=3500.0, currency="USD",
                          source="ws", timestamp=None)
        feed._update_price(point)
        result = feed.get_price("ETH/USD")
        assert result.price == 3500.0
        mock.get_price.assert_not_called()


# ── Connection state ─────────────────────────────────────────────────────────

class TestConnectionState:
    def test_initial_state(self, feed):
        assert not feed.is_connected
        assert feed.reconnect_count == 0

    def test_disconnect(self, feed):
        feed._running = True
        asyncio.run(feed.disconnect())
        assert not feed._running


# ── Config helper ─────────────────────────────────────────────────────────────

class TestUniswapConfig:
    def test_create_uniswap_ws_config(self):
        config = create_uniswap_ws_config("0x1234", "ethereum")
        assert "eth-mainnet" in config.url
        assert config.subscription_msg["method"] == "eth_subscribe"
        assert "logs" in config.subscription_msg["params"]

    def test_create_uniswap_ws_config_arbitrum(self):
        config = create_uniswap_ws_config("0x5678", "arbitrum")
        assert "arb" in config.url


# ── Message handling ──────────────────────────────────────────────────────────

class TestMessageHandling:
    @pytest.mark.asyncio
    async def test_handle_valid_message(self, feed):
        msg = json.dumps({"asset": "ETH/USD", "price": 3500.0})
        await feed._handle_message(msg)
        assert feed.get_price("ETH/USD").price == 3500.0

    @pytest.mark.asyncio
    async def test_handle_invalid_json(self, feed):
        await feed._handle_message("not json {")  # Should not raise
        assert feed.get_price("ETH/USD") is None

    @pytest.mark.asyncio
    async def test_handle_missing_fields(self, feed):
        msg = json.dumps({"foo": "bar"})
        await feed._handle_message(msg)  # Should not raise, no price set
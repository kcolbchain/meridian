"""Unit tests for ChainlinkPriceFeed with mocked web3 contracts."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from src.oracle.price_feed import (
    ChainlinkPriceFeed,
    FeedConfig,
    InvalidPriceError,
    MockPriceFeed,
    PricePoint,
    StalePriceError,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_feed(
    answer: int = 2000_00000000,  # 2000 USD, 8 decimals
    updated_at: int | None = None,
    decimals: int = 8,
    round_id: int = 110680464442257320164,
    answered_in_round: int | None = None,
    fallback: MockPriceFeed | None = None,
    max_staleness: int = 3600,
) -> ChainlinkPriceFeed:
    """Build a ChainlinkPriceFeed with a mocked Web3 contract."""
    if updated_at is None:
        updated_at = int(time.time()) - 60  # 1 minute ago
    if answered_in_round is None:
        answered_in_round = round_id

    with patch("src.oracle.price_feed.Web3") as MockWeb3:
        mock_w3 = MagicMock()
        MockWeb3.return_value = mock_w3
        MockWeb3.HTTPProvider = MagicMock()
        MockWeb3.to_checksum_address = lambda addr: addr

        mock_contract = MagicMock()
        mock_w3.eth.contract.return_value = mock_contract

        mock_contract.functions.latestRoundData.return_value.call.return_value = (
            round_id, answer, updated_at - 10, updated_at, answered_in_round
        )
        mock_contract.functions.decimals.return_value.call.return_value = decimals
        mock_contract.functions.getRoundData.return_value.call.return_value = (
            round_id, answer, updated_at - 10, updated_at, answered_in_round
        )

        feed = ChainlinkPriceFeed(
            web3_provider="https://eth.llamarpc.com",
            feed_registry={
                "ETH/USD": FeedConfig(
                    address="0x5f4eC3Df9cbd43714FE2740f5E3616155c5b8419",
                    max_staleness=max_staleness,
                ),
            },
            fallback_feed=fallback,
        )
    return feed


# ---------------------------------------------------------------------------
# Tests — get_price
# ---------------------------------------------------------------------------

class TestGetPrice:
    def test_returns_valid_price(self):
        feed = _make_feed(answer=2500_00000000, decimals=8)
        point = feed.get_price("ETH/USD")
        assert point is not None
        assert point.price == 2500.0
        assert point.source == "chainlink"
        assert point.currency == "USD"
        assert point.asset == "ETH/USD"

    def test_confidence_degrades_with_age(self):
        # Price updated 1800s ago, max_staleness=3600  =>  confidence ≈ 0.5
        now = int(time.time())
        feed = _make_feed(updated_at=now - 1800, max_staleness=3600)
        point = feed.get_price("ETH/USD")
        assert point is not None
        assert 0.45 <= point.confidence <= 0.55

    def test_stale_price_triggers_fallback(self):
        fallback = MockPriceFeed(base_prices={"ETH/USD": 2400.0}, volatility=0.0)
        now = int(time.time())
        feed = _make_feed(
            updated_at=now - 7200,  # 2 hours old
            max_staleness=3600,
            fallback=fallback,
        )
        point = feed.get_price("ETH/USD")
        assert point is not None
        assert point.source == "mock"

    def test_negative_answer_triggers_fallback(self):
        fallback = MockPriceFeed(base_prices={"ETH/USD": 2400.0}, volatility=0.0)
        feed = _make_feed(answer=-1, fallback=fallback)
        point = feed.get_price("ETH/USD")
        assert point is not None
        assert point.source == "mock"

    def test_zero_answer_triggers_fallback(self):
        fallback = MockPriceFeed(base_prices={"ETH/USD": 2400.0}, volatility=0.0)
        feed = _make_feed(answer=0, fallback=fallback)
        point = feed.get_price("ETH/USD")
        assert point is not None
        assert point.source == "mock"

    def test_unregistered_asset_returns_none_without_fallback(self):
        feed = _make_feed()
        assert feed.get_price("UNKNOWN/USD") is None

    def test_unregistered_asset_uses_fallback(self):
        fallback = MockPriceFeed(base_prices={"UNKNOWN/USD": 42.0}, volatility=0.0)
        feed = _make_feed(fallback=fallback)
        point = feed.get_price("UNKNOWN/USD")
        assert point is not None
        assert point.price == pytest.approx(42.0, rel=0.01)

    def test_rpc_error_uses_fallback(self):
        fallback = MockPriceFeed(base_prices={"ETH/USD": 2400.0}, volatility=0.0)
        feed = _make_feed(fallback=fallback)
        # Simulate RPC failure
        feed._contracts["ETH/USD"].functions.latestRoundData.return_value.call.side_effect = (
            ConnectionError("RPC timeout")
        )
        point = feed.get_price("ETH/USD")
        assert point is not None
        assert point.source == "mock"

    def test_rpc_error_returns_none_without_fallback(self):
        feed = _make_feed()
        feed._contracts["ETH/USD"].functions.latestRoundData.return_value.call.side_effect = (
            ConnectionError("RPC timeout")
        )
        assert feed.get_price("ETH/USD") is None

    def test_round_id_stored(self):
        rid = 110680464442257320164
        feed = _make_feed(round_id=rid)
        point = feed.get_price("ETH/USD")
        assert point is not None
        assert point.round_id == rid

    def test_history_appended(self):
        feed = _make_feed()
        feed.get_price("ETH/USD")
        feed.get_price("ETH/USD")
        assert len(feed._history["ETH/USD"]) == 2


# ---------------------------------------------------------------------------
# Tests — get_historical
# ---------------------------------------------------------------------------

class TestGetHistorical:
    def test_returns_points(self):
        feed = _make_feed()
        points = feed.get_historical("ETH/USD", periods=3)
        # With a basic mock this may return up to 3 identical points
        assert isinstance(points, list)
        assert len(points) <= 3

    def test_unregistered_asset_returns_empty(self):
        feed = _make_feed()
        assert feed.get_historical("UNKNOWN/USD", periods=5) == []


# ---------------------------------------------------------------------------
# Tests — staleness & validation
# ---------------------------------------------------------------------------

class TestValidation:
    def test_stale_price_error_raised(self):
        feed = _make_feed()
        now = int(time.time())
        with pytest.raises(StalePriceError):
            feed._validate_round("ETH/USD", 2000_00000000, now - 7200, 1, 1)

    def test_invalid_price_error_raised(self):
        feed = _make_feed()
        now = int(time.time())
        with pytest.raises(InvalidPriceError):
            feed._validate_round("ETH/USD", -1, now, 1, 1)

    def test_zero_price_error(self):
        feed = _make_feed()
        now = int(time.time())
        with pytest.raises(InvalidPriceError):
            feed._validate_round("ETH/USD", 0, now, 1, 1)


# ---------------------------------------------------------------------------
# Tests — is_healthy / list_feeds
# ---------------------------------------------------------------------------

class TestHealthAndMetadata:
    def test_is_healthy_true(self):
        feed = _make_feed()
        assert feed.is_healthy("ETH/USD") is True

    def test_is_healthy_false_when_stale(self):
        now = int(time.time())
        feed = _make_feed(updated_at=now - 7200, max_staleness=3600)
        assert feed.is_healthy("ETH/USD") is False

    def test_list_feeds(self):
        feed = _make_feed()
        info = feed.list_feeds()
        assert "ETH/USD" in info
        assert "address" in info["ETH/USD"]
        assert "max_staleness" in info["ETH/USD"]
        assert "healthy" in info["ETH/USD"]


# ---------------------------------------------------------------------------
# Tests — FeedConfig
# ---------------------------------------------------------------------------

class TestFeedConfig:
    def test_defaults(self):
        with patch("src.oracle.price_feed.Web3") as MockWeb3:
            MockWeb3.to_checksum_address = lambda addr: addr
            cfg = FeedConfig(address="0xabc")
            assert cfg.max_staleness == 3600
            assert cfg.decimals is None

    def test_from_dict(self):
        """Ensure feed_registry accepts raw dicts."""
        with patch("src.oracle.price_feed.Web3") as MockWeb3:
            mock_w3 = MagicMock()
            MockWeb3.return_value = mock_w3
            MockWeb3.HTTPProvider = MagicMock()
            MockWeb3.to_checksum_address = lambda addr: addr
            mock_w3.eth.contract.return_value = MagicMock()

            feed = ChainlinkPriceFeed(
                web3_provider="http://localhost:8545",
                feed_registry={
                    "BTC/USD": {
                        "address": "0xF4030086522a5bEEa4988F8cA5B36dbC97BeE88c",
                        "max_staleness": 7200,
                    }
                },
            )
            assert "BTC/USD" in feed._feed_configs
            assert feed._feed_configs["BTC/USD"].max_staleness == 7200

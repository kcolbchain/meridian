"""Tests for price feed implementations."""

from unittest.mock import MagicMock, patch
from src.oracle.price_feed import ChainlinkPriceFeed, PricePoint


class TestChainlinkPriceFeed:
    def setup_method(self):
        self.feed = ChainlinkPriceFeed(
            provider_url="http://localhost:8545",
            feed_addresses={"ETH/USD": "0x1234"},
            heartbeat_threshold_seconds=3600,
        )

    def test_init_validates_provider(self):
        import pytest
        with pytest.raises(ValueError):
            ChainlinkPriceFeed(provider_url="", feed_addresses={"ETH/USD": "0x1234"})

    def test_init_validates_addresses(self):
        import pytest
        with pytest.raises(ValueError):
            ChainlinkPriceFeed(provider_url="http://localhost:8545", feed_addresses={})

    def test_get_price_returns_none_for_unknown_asset(self):
        feed = ChainlinkPriceFeed("http://localhost:8545", {"ETH/USD": "0x1234"})
        result = feed.get_price("UNKNOWN")
        assert result is None

    def test_get_historical_returns_empty_on_error(self):
        feed = ChainlinkPriceFeed("http://localhost:8545", {"ETH/USD": "0x1234"})
        result = feed.get_historical("UNKNOWN", 5)
        assert result == []

    def test_get_historical_empty_for_no_feeds(self):
        feed = ChainlinkPriceFeed("http://localhost:8545", {"ETH/USD": "0x1234"})
        with patch.object(feed._chainlink_oracle, "feed_addresses", {}):
            result = feed.get_historical("ETH/USD", 5)
            assert result == []

    def test_price_point_structure(self):
        from datetime import datetime
        pp = PricePoint(asset="BTC/USD", price=50000.0, currency="USD",
                        source="test", timestamp=datetime.utcnow(), confidence=0.95)
        assert pp.asset == "BTC/USD"
        assert pp.price == 50000.0
        assert pp.confidence == 0.95

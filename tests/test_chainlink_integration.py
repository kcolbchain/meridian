"""Integration test: read live Chainlink price feeds via a public RPC.

This test hits the Ethereum mainnet through a public RPC endpoint and reads
real Chainlink aggregator contracts. It is marked with ``pytest.mark.integration``
so it can be skipped during fast local runs::

    pytest -m "not integration"        # skip
    pytest -m integration              # run only integration tests
"""

import os

import pytest

from src.oracle.price_feed import ChainlinkPriceFeed, FeedConfig, PricePoint

# Public RPCs — override with MAINNET_RPC env var if needed
DEFAULT_RPC = "https://eth.llamarpc.com"

# Well-known Chainlink mainnet aggregator addresses
MAINNET_FEEDS = {
    "ETH/USD": FeedConfig(
        address="0x5f4eC3Df9cbd43714FE2740f5E3616155c5b8419",
        max_staleness=7200,
    ),
    "BTC/USD": FeedConfig(
        address="0xF4030086522a5bEEa4988F8cA5B36dbC97BeE88c",
        max_staleness=7200,
    ),
}


def _rpc_url() -> str:
    return os.environ.get("MAINNET_RPC", DEFAULT_RPC)


@pytest.fixture(scope="module")
def chainlink_feed() -> ChainlinkPriceFeed:
    return ChainlinkPriceFeed(
        web3_provider=_rpc_url(),
        feed_registry=MAINNET_FEEDS,
    )


@pytest.mark.integration
class TestChainlinkIntegration:
    """Live tests against Ethereum mainnet Chainlink feeds."""

    def test_eth_usd_price_in_range(self, chainlink_feed: ChainlinkPriceFeed):
        """ETH/USD should be within a plausible range."""
        point = chainlink_feed.get_price("ETH/USD")
        assert point is not None
        assert isinstance(point, PricePoint)
        assert point.source == "chainlink"
        # Broad sanity range — ETH between $100 and $100k
        assert 100 < point.price < 100_000, f"ETH/USD price out of range: {point.price}"
        assert point.confidence > 0.0

    def test_btc_usd_price_in_range(self, chainlink_feed: ChainlinkPriceFeed):
        """BTC/USD should be within a plausible range."""
        point = chainlink_feed.get_price("BTC/USD")
        assert point is not None
        assert 1_000 < point.price < 1_000_000, f"BTC/USD price out of range: {point.price}"

    def test_historical_returns_data(self, chainlink_feed: ChainlinkPriceFeed):
        """get_historical should return at least 1 round."""
        points = chainlink_feed.get_historical("ETH/USD", periods=3)
        assert len(points) >= 1
        for p in points:
            assert p.price > 0

    def test_is_healthy(self, chainlink_feed: ChainlinkPriceFeed):
        assert chainlink_feed.is_healthy("ETH/USD") is True

    def test_list_feeds_metadata(self, chainlink_feed: ChainlinkPriceFeed):
        info = chainlink_feed.list_feeds()
        assert "ETH/USD" in info
        assert "BTC/USD" in info
        assert info["ETH/USD"]["healthy"] is True

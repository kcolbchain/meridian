import os
import pytest
from unittest.mock import patch, MagicMock, call

from src.connectors.chainlink import (
    ChainlinkOracle,
    OracleConnectionError,
    OracleFeedNotFound,
    OracleStalePriceError,
    OracleError,
)

# Sepolia ETH/USD Chainlink feed (https://docs.chain.link/data-feeds/price-feeds/addresses?network=ethereum&page=1)
SEPOLIA_ETH_USD_FEED = "0x694AA1769357215DE4FAC081bf1f309aDC325306"
TEST_ASSET = "ETH/USD"


@pytest.fixture(scope="module")
def oracle():
    """Live integration fixture — skipped unless WEB3_PROVIDER_URL_SEPOLIA is set."""
    provider_url = os.environ.get("WEB3_PROVIDER_URL_SEPOLIA")
    if not provider_url:
        pytest.skip("WEB3_PROVIDER_URL_SEPOLIA not set")
    return ChainlinkOracle(
        provider_url=provider_url,
        feed_addresses={TEST_ASSET: SEPOLIA_ETH_USD_FEED},
        heartbeat_threshold_seconds=7200,
    )


def test_get_price_live(oracle):
    """Verify we can fetch a non-zero ETH/USD price from Sepolia."""
    price = oracle.get_price(TEST_ASSET)
    assert price > 0, f"Expected positive price, got {price}"
    assert price < 1_000_000, f"Price looks unreasonably large: {price}"


def test_stale_price_raises(oracle):
    """Verify OracleStalePriceError when heartbeat threshold is very low."""
    strict_oracle = ChainlinkOracle(
        provider_url=oracle.provider_url,
        feed_addresses={TEST_ASSET: SEPOLIA_ETH_USD_FEED},
        heartbeat_threshold_seconds=1,  # 1 second — virtually always stale
    )
    with pytest.raises(OracleStalePriceError):
        strict_oracle.get_price(TEST_ASSET)


# ── Unit tests (no network) ────────────────────────────────────────────────────

def _make_oracle(provider="http://localhost:8545"):
    return ChainlinkOracle(
        provider_url=provider,
        feed_addresses={TEST_ASSET: "0x694AA1769357215DE4FAC081bf1f309aDC325306"},
    )


def test_unknown_pair_raises():
    oracle = _make_oracle()
    with patch.object(oracle, "w3"):
        with pytest.raises(OracleFeedNotFound):
            oracle.get_price("UNKNOWN/PAIR")


def test_connection_error_wraps():
    oracle = _make_oracle()
    with patch("src.connectors.chainlink.Web3") as MockWeb3:
        MockWeb3.return_value.is_connected.return_value = False
        MockWeb3.HTTPProvider.return_value = MagicMock()
        with pytest.raises(OracleConnectionError):
            _ = oracle.w3


def test_stale_price_unit():
    import time
    oracle = _make_oracle()
    mock_w3 = MagicMock()
    mock_contract = MagicMock()
    mock_contract.functions.decimals.return_value.call.return_value = 8
    # Return updatedAt = 2 hours ago
    stale_ts = int(time.time()) - 7201
    mock_contract.functions.latestRoundData.return_value.call.return_value = (
        1, 200000000000, stale_ts, stale_ts, 1
    )
    mock_w3.is_connected.return_value = True
    mock_w3.to_checksum_address.side_effect = lambda x: x
    mock_w3.eth.contract.return_value = mock_contract
    oracle._w3 = mock_w3
    with pytest.raises(OracleStalePriceError):
        oracle.get_price(TEST_ASSET)


def test_valid_price_unit():
    import time
    oracle = _make_oracle()
    mock_w3 = MagicMock()
    mock_contract = MagicMock()
    mock_contract.functions.decimals.return_value.call.return_value = 8
    now = int(time.time())
    mock_contract.functions.latestRoundData.return_value.call.return_value = (
        1, 200000000000, now - 60, now - 60, 1  # 000.00 price
    )
    mock_w3.is_connected.return_value = True
    mock_w3.to_checksum_address.side_effect = lambda x: x
    mock_w3.eth.contract.return_value = mock_contract
    oracle._w3 = mock_w3
    price = oracle.get_price(TEST_ASSET)
    assert abs(price - 2000.0) < 0.01

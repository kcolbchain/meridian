import os
import pytest
import time
from unittest.mock import patch, MagicMock

# Assuming the project root is the current working directory for imports
from src.connectors.chainlink import (
    ChainlinkOracle,
    OracleConnectionError,
    OracleFeedNotFound,
    OracleStalePriceError,
    OracleError,
)

# Use a known Chainlink Sepolia ETH/USD feed for integration testing
# Sepolia ETH/USD: 0x694AA1769357215Ee4f0fSingleton702d8dC6A645fEcbE
# (Found from https://docs.chain.link/data-feeds/price-feeds/evm/sepolia)
SEPOLIA_ETH_USD_FEED = "0x694AA1769357215Ee4f0fSingleton702d8dC6A645fEcbE"
TEST_ASSET_PAIR = "ETH/USD"

@pytest.fixture(scope="module")
def chainlink_oracle_instance():
    """Fixture to initialize ChainlinkOracle with a Sepolia RPC URL."""
    # Ensure WEB3_PROVIDER_URL_SEPOLIA is set in your environment for integration tests
    # e.g., export WEB3_PROVIDER_URL_SEPOLIA="https://sepolia.infura.io/v3/YOUR_INFURA_PROJECT_ID"
    provider_url = os.environ.get("WEB3_PROVIDER_URL_SEPOLIA")
    if not provider_url:
        pytest.skip("WEB3_PROVIDER_URL_SEPOLIA not set, skipping integration tests.")
    
    # Use a high heartbeat threshold for integration tests to avoid false positives
    # as network latency or Chainlink update frequency might be an issue for very low thresholds.
    oracle = ChainlinkOracle(
        provider_url=provider_url,
        feed_addresses={TEST_ASSET_PAIR: SEPOLIA_ETH_USD_FEED},
        heartbeat_threshold_seconds=7200 # 2 hours
    )
    return oracle

def test_chainlink_oracle_connects_and_fetches_price(chainlink_oracle_instance):
    """
    Integration test to ensure the oracle can connect to Sepolia and fetch a price.
    """
    try:
        price = chainlink_oracle_instance.get_price(TEST_ASSET_PAIR)
        print(f"Fetched {TEST_ASSET_PAIR} price: {price}")
        assert isinstance(price, float)
        assert price > 0  # Price should be positive
        # ETH price should be in a reasonable range (e.g., $1000 - $100,000)
        assert 1000 < price < 100000 
    except OracleConnectionError as e:
        pytest.fail(f"Failed to connect to RPC provider: {e}")
    except OracleFeedNotFound as e:
        pytest.fail(f"Feed not found or invalid: {e}")
    except OracleStalePriceError as e:
        pytest.fail(f"Price considered stale (likely due to network issues or slow updates): {e}")
    except OracleError as e:
        pytest.fail(f"An unexpected oracle error occurred: {e}")

def test_chainlink_oracle_handles_invalid_feed_address(chainlink_oracle_instance):
    """
    Test handling of an invalid feed address.
    """
    invalid_feed_address = "0x0000000000000000000000000000000000000001" # A known invalid address
    asset_pair = "INVALID/PAIR"
    oracle_with_invalid = ChainlinkOracle(
        provider_url=chainlink_oracle_instance.provider_url,
        feed_addresses={asset_pair: invalid_feed_address},
        heartbeat_threshold_seconds=3600
    )
    with pytest.raises(OracleError, match="Chainlink contract call failed.*"):
        oracle_with_invalid.get_price(asset_pair)

def test_chainlink_oracle_handles_unconfigured_feed(chainlink_oracle_instance):
    """
    Test handling of an asset pair that is not configured.
    """
    with pytest.raises(OracleFeedNotFound, match="No Chainlink feed address configured"):
        chainlink_oracle_instance.get_price("NOT_CONFIGURED/PAIR")

def test_chainlink_oracle_stale_price_detection(chainlink_oracle_instance):
    """
    Test that stale price detection works by mocking time.
    """
    asset_pair = TEST_ASSET_PAIR
    
    # Create a new oracle instance with a specific heartbeat threshold for testing
    stale_oracle_check = ChainlinkOracle(
        provider_url=chainlink_oracle_instance.provider_url,
        feed_addresses={asset_pair: SEPOLIA_ETH_USD_FEED},
        heartbeat_threshold_seconds=5 # A small threshold for testing
    )

    # First, get the actual `updatedAt` from the feed
    contract = stale_oracle_check._get_contract(asset_pair)
    round_data = contract.functions.latestRoundData().call()
    actual_updated_at = round_data[3]

    # Calculate a mock current timestamp that is significantly past the `updatedAt` + threshold
    mock_current_timestamp_stale = actual_updated_at + stale_oracle_check.heartbeat_threshold_seconds + 100 # +100s to ensure staleness

    # Now, call get_price, but mock time.time() to return the calculated stale timestamp
    with patch('src.connectors.chainlink.time.time', return_value=mock_current_timestamp_stale):
        with pytest.raises(OracleStalePriceError, match=f"Price for {asset_pair} is stale"):
            stale_oracle_check.get_price(asset_pair)

    # Test when the price is NOT stale (mock time to be just before threshold)
    mock_current_timestamp_fresh = actual_updated_at + stale_oracle_check.heartbeat_threshold_seconds - 1 # 1s before threshold
    with patch('src.connectors.chainlink.time.time', return_value=mock_current_timestamp_fresh):
        price = stale_oracle_check.get_price(asset_pair)
        assert isinstance(price, float)
        assert price > 0


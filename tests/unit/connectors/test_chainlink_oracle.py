import time
from unittest.mock import MagicMock

import pytest

from src.connectors.chainlink import (
    CHAINLINK_ABI,
    ChainlinkOracle,
    OracleError,
    OracleStalePriceError,
)
from src.oracle.price_feed import ChainlinkPriceFeed

TEST_ASSET = "ETH/USD"
TEST_ADDRESS = "0x694AA1769357215DE4FAC081bf1f309aDC325306"


def _mock_oracle(rounds: dict[int, tuple], latest_round_id: int = 5) -> ChainlinkOracle:
    oracle = ChainlinkOracle(
        provider_url="http://localhost:8545",
        feed_addresses={TEST_ASSET: TEST_ADDRESS},
        heartbeat_threshold_seconds=3600,
    )

    contract = MagicMock()
    contract.functions.decimals.return_value.call.return_value = 8
    contract.functions.latestRoundData.return_value.call.return_value = rounds[latest_round_id]
    contract.functions.getRoundData.side_effect = (
        lambda round_id: MagicMock(call=MagicMock(return_value=rounds[round_id]))
    )

    w3 = MagicMock()
    w3.is_connected.return_value = True
    w3.to_checksum_address.side_effect = lambda address: address
    w3.eth.contract.return_value = contract
    oracle._w3 = w3
    return oracle


def test_chainlink_abi_includes_get_round_data():
    names = {entry.get("name") for entry in CHAINLINK_ABI}
    assert "latestRoundData" in names
    assert "getRoundData" in names


def test_get_latest_round_returns_normalized_data():
    now = int(time.time())
    oracle = _mock_oracle({5: (5, 200_000_000_000, now - 30, now - 30, 5)})

    round_data = oracle.get_latest_round(TEST_ASSET)

    assert round_data.round_id == 5
    assert round_data.price == 2000.0
    assert round_data.timestamp.timestamp() == pytest.approx(now - 30)


def test_get_rounds_walks_backward_from_latest_round():
    now = int(time.time())
    rounds = {
        5: (5, 205_000_000_000, now - 30, now - 30, 5),
        4: (4, 204_000_000_000, now - 60, now - 60, 4),
        3: (3, 203_000_000_000, now - 90, now - 90, 3),
    }
    oracle = _mock_oracle(rounds)

    historical = oracle.get_rounds(TEST_ASSET, 3)

    assert [round_data.round_id for round_data in historical] == [5, 4, 3]
    assert [round_data.price for round_data in historical] == [2050.0, 2040.0, 2030.0]


def test_get_rounds_returns_empty_for_non_positive_periods():
    oracle = _mock_oracle({5: (5, 200_000_000_000, 1, 1, 5)})
    assert oracle.get_rounds(TEST_ASSET, 0) == []


def test_get_latest_round_rejects_stale_latest_price():
    stale_ts = int(time.time()) - 7200
    oracle = _mock_oracle({5: (5, 200_000_000_000, stale_ts, stale_ts, 5)})

    with pytest.raises(OracleStalePriceError):
        oracle.get_latest_round(TEST_ASSET)


def test_get_round_rejects_invalid_historical_answer():
    now = int(time.time())
    oracle = _mock_oracle(
        {
            5: (5, 200_000_000_000, now - 30, now - 30, 5),
            4: (4, 0, now - 60, now - 60, 4),
        }
    )

    with pytest.raises(OracleError):
        oracle.get_round(TEST_ASSET, 4)


def test_chainlink_price_feed_get_historical_returns_price_points():
    now = int(time.time())
    feed = ChainlinkPriceFeed(
        provider_url="http://localhost:8545",
        feed_addresses={TEST_ASSET: TEST_ADDRESS},
    )
    feed._chainlink_oracle = _mock_oracle(
        {
            5: (5, 205_000_000_000, now - 30, now - 30, 5),
            4: (4, 204_000_000_000, now - 60, now - 60, 4),
        }
    )

    points = feed.get_historical(TEST_ASSET, 2)

    assert [point.price for point in points] == [2050.0, 2040.0]
    assert all(point.asset == TEST_ASSET for point in points)
    assert all(point.source == "chainlink" for point in points)
    assert all(point.confidence == 0.99 for point in points)

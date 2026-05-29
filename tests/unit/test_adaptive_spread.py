"""Unit tests for adaptive spread quote generation."""

import pytest

from src.strategies.adaptive_spread import (
    AdaptiveSpreadParams,
    compute_adaptive_quotes,
)


def test_zero_volatility_clips_to_min_spread():
    params = AdaptiveSpreadParams(
        base_spread_bps=10,
        min_spread_bps=50,
        max_spread_bps=1_000,
    )

    quotes = compute_adaptive_quotes(
        mid_price=100.0,
        volatility=0.0,
        inventory_ratio=0.0,
        params=params,
    )

    assert quotes["spread_bps"] == pytest.approx(50.0)


def test_high_volatility_clips_to_max_spread():
    params = AdaptiveSpreadParams(
        base_spread_bps=100,
        vol_multiplier=20.0,
        min_spread_bps=50,
        max_spread_bps=1_000,
    )

    quotes = compute_adaptive_quotes(
        mid_price=100.0,
        volatility=1.0,
        inventory_ratio=0.0,
        params=params,
    )

    assert quotes["spread_bps"] == pytest.approx(1_000.0)


def test_neutral_inventory_quotes_are_symmetric_around_mid():
    mid_price = 100.0
    quotes = compute_adaptive_quotes(
        mid_price=mid_price,
        volatility=0.25,
        inventory_ratio=0.0,
        params=AdaptiveSpreadParams(base_spread_bps=200),
    )

    bid_distance = mid_price - quotes["bid"]
    ask_distance = quotes["ask"] - mid_price

    assert quotes["skew"] == pytest.approx(0.0)
    assert bid_distance == pytest.approx(ask_distance)


def test_long_inventory_widens_bid_and_tightens_ask():
    mid_price = 100.0
    params = AdaptiveSpreadParams(base_spread_bps=200)

    neutral = compute_adaptive_quotes(mid_price, 0.0, 0.0, params)
    long_base = compute_adaptive_quotes(mid_price, 0.0, 1.0, params)

    assert long_base["skew"] > 0
    assert long_base["bid"] < neutral["bid"]
    assert long_base["ask"] < neutral["ask"]


def test_short_inventory_tightens_bid_and_widens_ask():
    mid_price = 100.0
    params = AdaptiveSpreadParams(base_spread_bps=200)

    neutral = compute_adaptive_quotes(mid_price, 0.0, 0.0, params)
    short_base = compute_adaptive_quotes(mid_price, 0.0, -1.0, params)

    assert short_base["skew"] < 0
    assert short_base["bid"] > neutral["bid"]
    assert short_base["ask"] > neutral["ask"]


@pytest.mark.parametrize("volatility", [0.0, 0.15, 0.5, 1.0])
@pytest.mark.parametrize("inventory_ratio", [-1.0, -0.5, 0.0, 0.5, 1.0])
def test_quotes_keep_mid_inside_bid_ask(volatility, inventory_ratio):
    mid_price = 100.0
    quotes = compute_adaptive_quotes(
        mid_price=mid_price,
        volatility=volatility,
        inventory_ratio=inventory_ratio,
        params=AdaptiveSpreadParams(),
    )

    assert quotes["bid"] < mid_price < quotes["ask"]


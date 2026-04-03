"""Adaptive spread strategy — adjusts to volatility and inventory."""

from dataclasses import dataclass
import numpy as np


@dataclass
class AdaptiveSpreadParams:
    base_spread_bps: float = 150
    vol_multiplier: float = 5.0
    inventory_skew_factor: float = 0.5
    min_spread_bps: float = 50
    max_spread_bps: float = 1000


def compute_adaptive_quotes(
    mid_price: float,
    volatility: float,
    inventory_ratio: float,
    params: AdaptiveSpreadParams,
) -> dict:
    """Compute adaptive bid/ask quotes.

    Args:
        mid_price: Current mid price
        volatility: Normalized volatility (0-1)
        inventory_ratio: -1 (all quote) to +1 (all base)
        params: Strategy parameters
    """
    # Base spread + volatility component
    spread_bps = params.base_spread_bps + volatility * params.vol_multiplier * 100
    spread_bps = np.clip(spread_bps, params.min_spread_bps, params.max_spread_bps)

    # Skew: when inventory is positive (long), widen bid, tighten ask
    skew = inventory_ratio * params.inventory_skew_factor
    bid_spread = spread_bps * (1 + skew) / 10000
    ask_spread = spread_bps * (1 - skew) / 10000

    return {
        "bid": mid_price * (1 - bid_spread / 2),
        "ask": mid_price * (1 + ask_spread / 2),
        "spread_bps": float(spread_bps),
        "skew": skew,
    }

"""Constant spread strategy — simple baseline."""

from dataclasses import dataclass


@dataclass
class ConstantSpreadParams:
    spread_bps: float = 200  # 2% total spread
    order_size_pct: float = 0.1  # 10% of available balance per side


def compute_quotes(mid_price: float, params: ConstantSpreadParams) -> dict:
    """Compute bid/ask quotes around a mid price."""
    half_spread = mid_price * (params.spread_bps / 10000) / 2
    return {
        "bid": mid_price - half_spread,
        "ask": mid_price + half_spread,
        "spread_bps": params.spread_bps,
    }

"""Avellaneda-Stoikov optimal market-making strategy.

Implements the model from "High-frequency trading in a limit order book"
(Avellaneda & Stoikov, 2008).

Reservation price:  r(s,q,t) = s - q * γ * σ² * (T - t)
Optimal spread:     δ(q,t)   = γ * σ² * (T - t) + (2/γ) * ln(1 + γ/κ)

Where:
    s = mid price
    q = inventory (signed, positive = long)
    γ = risk aversion parameter
    σ = volatility of the asset
    T = trading horizon
    t = current time
    κ = order arrival intensity
"""

from dataclasses import dataclass, field
import math

import numpy as np


@dataclass
class AvellanedaStoikovParams:
    """Parameters for the Avellaneda-Stoikov model."""

    risk_aversion: float = 0.1
    volatility_window: int = 50
    horizon: float = 1.0
    order_arrival_intensity: float = 1.5
    position_limit: float = 100.0
    min_spread_bps: float = 10.0
    max_spread_bps: float = 2000.0
    order_size_pct: float = 0.1


class VolatilityEstimator:
    """Real-time volatility estimation from a rolling window of prices."""

    def __init__(self, window: int = 50):
        self.window = window
        self._prices: list[float] = []

    def update(self, price: float) -> None:
        self._prices.append(price)
        if len(self._prices) > self.window + 1:
            self._prices = self._prices[-(self.window + 1):]

    def estimate(self) -> float:
        """Return annualized volatility of log-returns.

        Falls back to a moderate default when not enough data is available.
        """
        if len(self._prices) < 2:
            return 0.02  # sensible default

        prices = np.array(self._prices)
        log_returns = np.diff(np.log(prices))
        return float(np.std(log_returns)) if len(log_returns) > 0 else 0.02

    @property
    def ready(self) -> bool:
        return len(self._prices) >= 2


def reservation_price(
    mid: float,
    inventory: float,
    risk_aversion: float,
    volatility: float,
    time_remaining: float,
) -> float:
    """Compute the reservation (indifference) price.

    r = s - q * γ * σ² * (T - t)
    """
    return mid - inventory * risk_aversion * (volatility ** 2) * time_remaining


def optimal_spread(
    risk_aversion: float,
    volatility: float,
    time_remaining: float,
    intensity: float,
) -> float:
    """Compute the optimal spread around the reservation price.

    δ = γ * σ² * (T - t) + (2/γ) * ln(1 + γ/κ)
    """
    if risk_aversion <= 0 or intensity <= 0:
        raise ValueError("risk_aversion and intensity must be positive")

    inventory_component = risk_aversion * (volatility ** 2) * time_remaining
    adverse_selection = (2.0 / risk_aversion) * math.log(1.0 + risk_aversion / intensity)
    return inventory_component + adverse_selection


def compute_quotes(
    mid_price: float,
    inventory: float,
    params: AvellanedaStoikovParams,
    time_remaining: float,
    volatility: float,
) -> dict:
    """Compute optimal bid/ask quotes using the Avellaneda-Stoikov model.

    Returns a dict with bid, ask, reservation_price, spread, and spread_bps.
    """
    r = reservation_price(
        mid=mid_price,
        inventory=inventory,
        risk_aversion=params.risk_aversion,
        volatility=volatility,
        time_remaining=time_remaining,
    )

    delta = optimal_spread(
        risk_aversion=params.risk_aversion,
        volatility=volatility,
        time_remaining=time_remaining,
        intensity=params.order_arrival_intensity,
    )

    half_spread = delta / 2.0
    bid = r - half_spread
    ask = r + half_spread

    raw_spread_bps = (ask - bid) / mid_price * 10000 if mid_price > 0 else 0.0
    spread_bps = float(np.clip(raw_spread_bps, params.min_spread_bps, params.max_spread_bps))

    # Re-derive bid/ask if spread was clamped
    if spread_bps != raw_spread_bps:
        clamped_half = mid_price * (spread_bps / 10000) / 2.0
        bid = r - clamped_half
        ask = r + clamped_half

    return {
        "bid": bid,
        "ask": ask,
        "reservation_price": r,
        "spread": ask - bid,
        "spread_bps": spread_bps,
        "volatility": volatility,
        "time_remaining": time_remaining,
    }

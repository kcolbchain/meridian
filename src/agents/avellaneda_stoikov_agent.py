"""Avellaneda-Stoikov market-making agent.

Wraps the A-S optimal quoting model into the BaseAgent lifecycle,
adding real-time volatility estimation, inventory tracking and
position-limit enforcement.
"""

import logging
from typing import Optional

from .base_agent import BaseAgent, Order, Side
from ..strategies.avellaneda_stoikov import (
    AvellanedaStoikovParams,
    VolatilityEstimator,
    compute_quotes,
)

logger = logging.getLogger(__name__)


class AvellanedaStoikovAgent(BaseAgent):
    """Market-making agent driven by the Avellaneda-Stoikov model."""

    def __init__(self, agent_id: str, config: dict, oracle=None):
        super().__init__(agent_id, config)
        self.oracle = oracle

        self.params = AvellanedaStoikovParams(
            risk_aversion=config.get("risk_aversion", 0.1),
            volatility_window=config.get("volatility_window", 50),
            horizon=config.get("horizon", 1.0),
            order_arrival_intensity=config.get("order_arrival_intensity", 1.5),
            position_limit=config.get("position_limit", 100.0),
            min_spread_bps=config.get("min_spread_bps", 10.0),
            max_spread_bps=config.get("max_spread_bps", 2000.0),
            order_size_pct=config.get("order_size_pct", 0.1),
        )

        self.vol_estimator = VolatilityEstimator(window=self.params.volatility_window)
        self._tick_count: int = 0
        self._total_ticks: int = config.get("total_ticks", 1000)
        self.last_mid: Optional[float] = None

    # -- time helpers --------------------------------------------------

    @property
    def time_elapsed_fraction(self) -> float:
        """Fraction of the trading horizon that has elapsed."""
        if self._total_ticks <= 0:
            return 0.0
        return min(self._tick_count / self._total_ticks, 1.0)

    @property
    def time_remaining(self) -> float:
        """Time remaining on the trading horizon (unitless, 0-T)."""
        return self.params.horizon * (1.0 - self.time_elapsed_fraction)

    # -- BaseAgent interface -------------------------------------------

    def evaluate_market(self, market_data: dict) -> dict:
        oracle_price = market_data.get("oracle_price")
        on_chain_price = market_data.get("on_chain_price")

        mid_price = oracle_price or on_chain_price
        if mid_price is None:
            return {"tradeable": False, "reason": "no_price_data"}

        self.vol_estimator.update(mid_price)
        self.last_mid = mid_price
        self._tick_count += 1

        volatility = self.vol_estimator.estimate()

        signals = {
            "tradeable": True,
            "mid_price": mid_price,
            "volatility": volatility,
            "inventory": self.position.net_exposure,
            "time_remaining": self.time_remaining,
        }
        self.log_event("market_eval", signals)
        return signals

    def execute_strategy(self, signals: dict) -> list[Order]:
        if not signals.get("tradeable"):
            return []

        mid = signals["mid_price"]
        volatility = signals["volatility"]
        inventory = signals["inventory"]
        t_rem = signals["time_remaining"]

        # Avoid degenerate case at horizon end
        if t_rem <= 0:
            t_rem = 1e-6

        quotes = compute_quotes(
            mid_price=mid,
            inventory=inventory,
            params=self.params,
            time_remaining=t_rem,
            volatility=volatility,
        )

        bid_price = quotes["bid"]
        ask_price = quotes["ask"]

        # Order sizing
        max_order_pct = self.params.order_size_pct
        bid_size = self.position.quote_balance * max_order_pct / bid_price if bid_price > 0 else 0
        ask_size = self.position.base_balance * max_order_pct if self.position.base_balance > 0 else 0

        orders: list[Order] = []

        if bid_size > 0 and inventory < self.params.position_limit:
            orders.append(Order(
                side=Side.BID,
                price=round(bid_price, 6),
                size=round(bid_size, 6),
            ))

        if ask_size > 0 and inventory > -self.params.position_limit:
            orders.append(Order(
                side=Side.ASK,
                price=round(ask_price, 6),
                size=round(ask_size, 6),
            ))

        self.log_event("orders_placed", {
            "reservation_price": quotes["reservation_price"],
            "spread_bps": quotes["spread_bps"],
            "bid": bid_price if bid_size > 0 else None,
            "ask": ask_price if ask_size > 0 else None,
        })

        return orders

    def rebalance(self) -> list[Order]:
        """Aggressively reduce inventory when position limit is breached."""
        if self.last_mid is None:
            return []

        exposure = self.position.net_exposure
        limit = self.params.position_limit

        if abs(exposure) <= limit:
            return []

        excess = abs(exposure) - limit
        if exposure > 0:
            price = self.last_mid * 0.998
            return [Order(side=Side.ASK, price=round(price, 6), size=round(excess, 6))]
        else:
            price = self.last_mid * 1.002
            return [Order(side=Side.BID, price=round(price, 6), size=round(excess, 6))]

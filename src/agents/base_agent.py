"""Base agent class for autonomous market making."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional
import logging

logger = logging.getLogger(__name__)


class Side(Enum):
    BID = "bid"
    ASK = "ask"


@dataclass
class Order:
    side: Side
    price: float
    size: float
    timestamp: datetime = field(default_factory=datetime.utcnow)
    order_id: Optional[str] = None


@dataclass
class Fill:
    side: Side
    price: float
    size: float
    fee: float
    timestamp: datetime = field(default_factory=datetime.utcnow)


@dataclass
class Position:
    base_balance: float = 0.0
    quote_balance: float = 0.0
    avg_entry_price: float = 0.0
    realized_pnl: float = 0.0

    @property
    def net_exposure(self) -> float:
        return self.base_balance

    @property
    def unrealized_pnl(self) -> float:
        return 0.0  # requires current price — computed by agent

    def apply_fill(self, fill: Fill):
        if fill.side == Side.BID:
            total_cost = self.base_balance * self.avg_entry_price + fill.size * fill.price
            self.base_balance += fill.size
            self.quote_balance -= fill.size * fill.price + fill.fee
            if self.base_balance > 0:
                self.avg_entry_price = total_cost / self.base_balance
        elif fill.side == Side.ASK:
            pnl = fill.size * (fill.price - self.avg_entry_price) - fill.fee
            self.realized_pnl += pnl
            self.base_balance -= fill.size
            self.quote_balance += fill.size * fill.price - fill.fee

        logger.info(
            f"Fill applied: {fill.side.value} {fill.size} @ {fill.price} | "
            f"Position: {self.base_balance:.4f} base, {self.quote_balance:.2f} quote | "
            f"Realized PnL: {self.realized_pnl:.2f}"
        )


class BaseAgent(ABC):
    """Abstract base class for all market-making agents."""

    def __init__(self, agent_id: str, config: dict):
        self.agent_id = agent_id
        self.config = config
        self.position = Position(
            base_balance=config.get("initial_base", 0.0),
            quote_balance=config.get("initial_quote", 10000.0),
        )
        self.active_orders: list[Order] = []
        self.fill_history: list[Fill] = []
        self.is_running = False
        self._event_log: list[dict] = []

    @abstractmethod
    def evaluate_market(self, market_data: dict) -> dict:
        """Evaluate current market conditions. Returns signals dict."""
        ...

    @abstractmethod
    def execute_strategy(self, signals: dict) -> list[Order]:
        """Given market signals, generate orders."""
        ...

    @abstractmethod
    def rebalance(self) -> list[Order]:
        """Rebalance position based on inventory and risk limits."""
        ...

    def on_fill(self, fill: Fill):
        """Handle a fill event."""
        self.position.apply_fill(fill)
        self.fill_history.append(fill)
        self.log_event("fill", {
            "side": fill.side.value,
            "price": fill.price,
            "size": fill.size,
        })

    def tick(self, market_data: dict) -> list[Order]:
        """Main loop tick — evaluate market, run strategy, check rebalance."""
        signals = self.evaluate_market(market_data)
        orders = self.execute_strategy(signals)

        max_exposure = self.config.get("max_exposure", float("inf"))
        if abs(self.position.net_exposure) > max_exposure:
            rebalance_orders = self.rebalance()
            orders.extend(rebalance_orders)
            self.log_event("rebalance_triggered", {
                "exposure": self.position.net_exposure,
                "max": max_exposure,
            })

        self.active_orders = orders
        return orders

    def get_pnl(self, current_price: float) -> dict:
        unrealized = self.position.base_balance * (
            current_price - self.position.avg_entry_price
        )
        return {
            "realized": self.position.realized_pnl,
            "unrealized": unrealized,
            "total": self.position.realized_pnl + unrealized,
            "position_size": self.position.base_balance,
            "quote_balance": self.position.quote_balance,
        }

    def log_event(self, event_type: str, data: dict):
        entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "agent": self.agent_id,
            "event": event_type,
            **data,
        }
        self._event_log.append(entry)
        logger.debug(f"[{self.agent_id}] {event_type}: {data}")

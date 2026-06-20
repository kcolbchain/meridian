"""Base agent class for autonomous market making."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional
import logging

from src.settlement import CR8USDSettlement, SettlementConfig, SettlementType

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
        self._settlement = self._init_settlement(config)

    def _init_settlement(self, config: dict) -> CR8USDSettlement:
        asset_str = config.get("settlement_asset", "USDC")
        st = SettlementType.CR8_USD if asset_str == "CR8-USD" else (
            SettlementType.CUSTOM if asset_str not in ("USDC", "CR8-USD") else SettlementType.USDC
        )
        sc = SettlementConfig(
            settlement_asset=st,
            custom_asset_symbol=asset_str if st == SettlementType.CUSTOM else None,
            burn_toll_bps=config.get("burn_toll_bps", 0.0),
        )
        return CR8USDSettlement(sc)

    @property
    def settlement(self) -> CR8USDSettlement:
        return self._settlement

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

    def on_fill(self, fill: Fill, tx_id: str = ""):
        """Handle a fill event with optional settlement tracking."""
        self.position.apply_fill(fill)
        self.fill_history.append(fill)
        settlement_info = self._settlement.settle_fill(
            tx_id=tx_id or f"fill:{len(self.fill_history)}",
            side=fill.side.value,
            price=fill.price,
            size=fill.size,
            fee=fill.fee,
        )
        self.log_event("fill", {
            "side": fill.side.value,
            "price": fill.price,
            "size": fill.size,
            "settlement_asset": settlement_info["settlement_asset"],
            "burn_toll": settlement_info["burn_toll"],
        })
        if settlement_info["burn_toll"] > 0:
            self.log_event("burn_event", {
                "volume": settlement_info["volume"],
                "toll": settlement_info["burn_toll"],
                "asset": settlement_info["settlement_asset"],
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
        settlement_pnls = self._settlement.get_pnl()
        settlement_total = sum(p.realized_pnl for p in settlement_pnls)
        return {
            "realized": self.position.realized_pnl,
            "unrealized": unrealized,
            "total": self.position.realized_pnl + unrealized,
            "position_size": self.position.base_balance,
            "quote_balance": self.position.quote_balance,
            "settlement_asset": self._settlement.settlement_symbol,
            "settlement_pnl": settlement_total,
            "per_asset_pnl": [
                {"asset": p.asset, "realized": p.realized_pnl,
                 "burn_toll": p.burn_toll_paid, "volume": p.total_volume}
                for p in settlement_pnls
            ],
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

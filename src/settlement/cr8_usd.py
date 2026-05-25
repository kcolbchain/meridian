"""CR8-USD settlement for create-protocol agents.

Wraps stablecoin-toolkit mint/redeem so the agent operates in
CR8-USD instead of USDC. Tracks per-asset PnL and emits burn_event
logs for the protocol's burn dashboard.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional
import logging

logger = logging.getLogger(__name__)


class SettlementType(Enum):
    USDC = "USDC"
    CR8_USD = "CR8-USD"
    CUSTOM = "custom"


@dataclass
class SettlementConfig:
    settlement_asset: SettlementType = SettlementType.USDC
    custom_asset_symbol: Optional[str] = None
    burn_toll_bps: float = 0.0
    stablecoin_toolkit_url: Optional[str] = None


@dataclass
class PerAssetPnL:
    asset: str
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    burn_toll_paid: float = 0.0
    total_volume: float = 0.0


@dataclass
class BurnEvent:
    tx_id: str
    asset: str
    amount: float
    burn_toll: float
    timestamp: datetime = field(default_factory=datetime.utcnow)
    counterparty: str = ""


class CR8USDSettlement:
    """Handles CR8-USD mint/redeem settlement for create-protocol agents.

    In CR8-USD mode, fills are settled by minting (on buy) or redeeming
    (on sell) through a stablecoin-toolkit adapter. Burn toll cost is
    tracked separately and exposed via per-asset PnL.
    """

    def __init__(self, config: SettlementConfig):
        self.config = config
        self._asset_pnl: dict[str, PerAssetPnL] = {}
        self._burn_events: list[BurnEvent] = []

    @property
    def is_cr8_usd(self) -> bool:
        return self.config.settlement_asset == SettlementType.CR8_USD

    @property
    def settlement_symbol(self) -> str:
        if self.config.settlement_asset == SettlementType.CR8_USD:
            return "CR8-USD"
        elif self.config.settlement_asset == SettlementType.CUSTOM:
            return self.config.custom_asset_symbol or "CUSTOM"
        return "USDC"

    def settle_fill(
        self,
        tx_id: str,
        side: str,
        price: float,
        size: float,
        fee: float,
    ) -> dict:
        """Record a fill settlement and compute burn toll if applicable."""
        volume = price * size
        burn_toll = 0.0
        if self.is_cr8_usd and self.config.burn_toll_bps > 0:
            burn_toll = volume * (self.config.burn_toll_bps / 10000)

        pnl_key = self.settlement_symbol
        if pnl_key not in self._asset_pnl:
            self._asset_pnl[pnl_key] = PerAssetPnL(asset=pnl_key)

        pnl = self._asset_pnl[pnl_key]
        pnl.total_volume += volume
        pnl.burn_toll_paid += burn_toll
        realized = -fee - burn_toll
        pnl.realized_pnl += realized

        if burn_toll > 0:
            event = BurnEvent(
                tx_id=tx_id,
                asset=self.settlement_symbol,
                amount=volume,
                burn_toll=burn_toll,
                counterparty=side,
            )
            self._burn_events.append(event)
            logger.info(
                f"burn_event: {event.asset} vol={volume:.4f} "
                f"toll={burn_toll:.6f} tx={tx_id}"
            )

        return {
            "volume": volume,
            "fee": fee,
            "burn_toll": burn_toll,
            "settlement_asset": self.settlement_symbol,
        }

    def get_pnl(self, asset: Optional[str] = None) -> list[PerAssetPnL]:
        if asset:
            pnl = self._asset_pnl.get(asset)
            return [pnl] if pnl else []
        return list(self._asset_pnl.values())

    def get_burn_events(self, limit: int = 100) -> list[BurnEvent]:
        return self._burn_events[-limit:]

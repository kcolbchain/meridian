"""Oracle price feed interfaces for real-world assets."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
import random

from src.connectors.chainlink import (
    ChainlinkOracle,
    OracleConnectionError,
    OracleFeedNotFound,
    OracleStalePriceError,
    OracleError,
)


@dataclass
class PricePoint:
    asset: str
    price: float
    currency: str
    source: str
    timestamp: datetime
    confidence: float = 1.0  # 0-1, how reliable this price is


class BasePriceFeed(ABC):
    """Abstract price feed interface."""

    @abstractmethod
    def get_price(self, asset: str) -> Optional[PricePoint]:
        ...

    @abstractmethod
    def get_historical(self, asset: str, periods: int) -> list[PricePoint]:
        ...


class MockPriceFeed(BasePriceFeed):
    """Mock price feed for testing and simulation."""

    def __init__(self, base_prices: dict[str, float], volatility: float = 0.02):
        self.base_prices = base_prices
        self.volatility = volatility
        self._history: dict[str, list[PricePoint]] = {}

    def get_price(self, asset: str) -> Optional[PricePoint]:
        base = self.base_prices.get(asset)
        if base is None:
            return None
        price = base * (1 + random.gauss(0, self.volatility))
        point = PricePoint(
            asset=asset,
            price=price,
            currency="USD",
            source="mock",
            timestamp=datetime.utcnow(),
            confidence=0.95,
        )
        self._history.setdefault(asset, []).append(point)
        return point

    def get_historical(self, asset: str, periods: int) -> list[PricePoint]:
        history = self._history.get(asset, [])
        return history[-periods:]


class ChainlinkPriceFeed(BasePriceFeed):
    """Chainlink oracle integration using Chainlink Data Feeds."""

    def __init__(
        self,
        provider_url: str,
        feed_addresses: dict[str, str],
        heartbeat_threshold_seconds: int = 3600,
    ):
        if not provider_url:
            raise ValueError("provider_url cannot be empty")
        if not feed_addresses:
            raise ValueError("feed_addresses cannot be empty")
        self._chainlink_oracle = ChainlinkOracle(
            provider_url=provider_url,
            feed_addresses=feed_addresses,
            heartbeat_threshold_seconds=heartbeat_threshold_seconds,
        )

    def _to_price_point(self, asset: str, round_data) -> PricePoint:
        return PricePoint(
            asset=asset,
            price=round_data.price,
            currency="USD",  # Chainlink feeds typically report in USD
            source="chainlink",
            timestamp=round_data.timestamp,
            confidence=0.99,  # High confidence for Chainlink feeds
        )

    def get_price(self, asset: str) -> Optional[PricePoint]:
        try:
            return self._to_price_point(asset, self._chainlink_oracle.get_latest_round(asset))
        except OracleFeedNotFound:
            return None
        except OracleConnectionError as e:
            # Log connection issues, but return None as no price can be fetched
            # A more robust system would use a proper logging framework.
            print(f"Warning: Chainlink connection error for asset '{asset}': {e}")
            return None
        except OracleStalePriceError as e:
            # Stale price means the "real-time" requirement is not met, so raise an error.
            raise ValueError(f"Chainlink price for asset '{asset}' is stale: {e}") from e
        except OracleError as e:
            # Catch other general Chainlink oracle errors
            raise RuntimeError(f"Chainlink oracle error for asset '{asset}': {e}") from e
        except Exception as e:
            # Catch any unexpected errors
            raise RuntimeError(f"Unexpected error fetching Chainlink price for asset '{asset}': {e}") from e

    def get_historical(self, asset: str, periods: int) -> list[PricePoint]:
        try:
            return [
                self._to_price_point(asset, round_data)
                for round_data in self._chainlink_oracle.get_rounds(asset, periods)
            ]
        except OracleFeedNotFound:
            return []
        except OracleConnectionError as e:
            print(f"Warning: Chainlink connection error for asset '{asset}': {e}")
            return []
        except OracleError as e:
            raise RuntimeError(f"Chainlink oracle error for asset '{asset}': {e}") from e

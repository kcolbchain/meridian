"""Oracle price feed interfaces for real-world assets."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
import random


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
    """Chainlink oracle integration (placeholder — requires web3 connection)."""

    def __init__(self, web3_provider: str, feed_addresses: dict[str, str]):
        self.provider = web3_provider
        self.feeds = feed_addresses

    def get_price(self, asset: str) -> Optional[PricePoint]:
        # TODO: implement actual Chainlink read
        raise NotImplementedError("Connect web3 provider and implement latestRoundData() call")

    def get_historical(self, asset: str, periods: int) -> list[PricePoint]:
        raise NotImplementedError("Implement getRoundData() loop for historical prices")

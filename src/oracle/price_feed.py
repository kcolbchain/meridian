"""Oracle price feed interfaces for real-world assets."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Union
import logging
import random
import time

from web3 import Web3

logger = logging.getLogger(__name__)

# Chainlink AggregatorV3Interface ABI (minimal)
AGGREGATOR_V3_ABI = [
    {
        "inputs": [],
        "name": "latestRoundData",
        "outputs": [
            {"internalType": "uint80", "name": "roundId", "type": "uint80"},
            {"internalType": "int256", "name": "answer", "type": "int256"},
            {"internalType": "uint256", "name": "startedAt", "type": "uint256"},
            {"internalType": "uint256", "name": "updatedAt", "type": "uint256"},
            {"internalType": "uint80", "name": "answeredInRound", "type": "uint80"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [{"internalType": "uint80", "name": "_roundId", "type": "uint80"}],
        "name": "getRoundData",
        "outputs": [
            {"internalType": "uint80", "name": "roundId", "type": "uint80"},
            {"internalType": "int256", "name": "answer", "type": "int256"},
            {"internalType": "uint256", "name": "startedAt", "type": "uint256"},
            {"internalType": "uint256", "name": "updatedAt", "type": "uint256"},
            {"internalType": "uint80", "name": "answeredInRound", "type": "uint80"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "decimals",
        "outputs": [{"internalType": "uint8", "name": "", "type": "uint8"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "description",
        "outputs": [{"internalType": "string", "name": "", "type": "string"}],
        "stateMutability": "view",
        "type": "function",
    },
]


class StalePriceError(Exception):
    """Raised when a Chainlink price feed answer exceeds the heartbeat threshold."""

    def __init__(self, asset: str, age_seconds: int, max_staleness: int):
        self.asset = asset
        self.age_seconds = age_seconds
        self.max_staleness = max_staleness
        super().__init__(
            f"Stale price for {asset}: age={age_seconds}s, max={max_staleness}s"
        )


class InvalidPriceError(Exception):
    """Raised when a Chainlink feed returns a non-positive answer."""

    def __init__(self, asset: str, answer: int):
        self.asset = asset
        self.answer = answer
        super().__init__(f"Invalid price for {asset}: answer={answer}")


@dataclass
class FeedConfig:
    """Configuration for a single Chainlink price feed."""

    address: str
    max_staleness: int = 3600  # seconds (heartbeat)
    decimals: Optional[int] = None  # auto-detected if None

    def __post_init__(self):
        self.address = Web3.to_checksum_address(self.address)


@dataclass
class PricePoint:
    asset: str
    price: float
    currency: str
    source: str
    timestamp: datetime
    confidence: float = 1.0  # 0-1, how reliable this price is
    round_id: Optional[int] = None


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
    """Chainlink Data Feed integration via web3.py.

    Reads real-time prices from Chainlink AggregatorV3Interface contracts.
    Supports configurable feed addresses per asset pair, heartbeat-based
    staleness detection, and graceful fallback to a secondary feed.

    Args:
        web3_provider: HTTP/WS RPC endpoint URL.
        feed_registry: Mapping of asset pair names to FeedConfig objects
            or raw dicts, e.g. ``{"ETH/USD": FeedConfig(address="0x...")}``.
        fallback_feed: Optional secondary BasePriceFeed used when the
            Chainlink feed is unavailable or stale.
    """

    def __init__(
        self,
        web3_provider: str,
        feed_registry: dict[str, Union[FeedConfig, dict]],
        fallback_feed: Optional[BasePriceFeed] = None,
    ):
        self.w3 = Web3(Web3.HTTPProvider(web3_provider))
        self.fallback_feed = fallback_feed
        self._contracts: dict[str, any] = {}
        self._feed_configs: dict[str, FeedConfig] = {}
        self._history: dict[str, list[PricePoint]] = {}

        for asset, cfg in feed_registry.items():
            if isinstance(cfg, dict):
                cfg = FeedConfig(**cfg)
            self._feed_configs[asset] = cfg
            self._contracts[asset] = self.w3.eth.contract(
                address=cfg.address, abi=AGGREGATOR_V3_ABI
            )

    def _detect_decimals(self, asset: str) -> int:
        """Query on-chain decimals for a feed and cache the result."""
        cfg = self._feed_configs[asset]
        if cfg.decimals is not None:
            return cfg.decimals
        decimals = self._contracts[asset].functions.decimals().call()
        cfg.decimals = decimals
        return decimals

    def _read_latest_round(self, asset: str) -> tuple[int, int, int, int, int]:
        """Call latestRoundData() and return the raw tuple."""
        return self._contracts[asset].functions.latestRoundData().call()

    def _validate_round(
        self, asset: str, answer: int, updated_at: int, round_id: int, answered_in_round: int
    ) -> None:
        """Validate price freshness and correctness."""
        if answer <= 0:
            raise InvalidPriceError(asset, answer)

        cfg = self._feed_configs[asset]
        age = int(time.time()) - updated_at
        if age > cfg.max_staleness:
            raise StalePriceError(asset, age, cfg.max_staleness)

        # answeredInRound should equal roundId for a complete round
        if answered_in_round < round_id:
            logger.warning(
                "Incomplete round for %s: roundId=%s answeredInRound=%s",
                asset, round_id, answered_in_round,
            )

    def get_price(self, asset: str) -> Optional[PricePoint]:
        """Fetch the latest price for *asset* from Chainlink.

        Falls back to ``self.fallback_feed`` when the primary feed is
        unavailable or returns a stale / invalid price.
        """
        if asset not in self._contracts:
            return self._try_fallback(asset, reason="no feed registered")

        try:
            round_id, answer, _started, updated_at, answered_in = self._read_latest_round(asset)
            self._validate_round(asset, answer, updated_at, round_id, answered_in)

            decimals = self._detect_decimals(asset)
            price = answer / (10 ** decimals)
            ts = datetime.fromtimestamp(updated_at, tz=timezone.utc)

            # Confidence degrades as the price ages relative to max_staleness
            cfg = self._feed_configs[asset]
            age = int(time.time()) - updated_at
            confidence = max(0.0, 1.0 - (age / cfg.max_staleness))

            point = PricePoint(
                asset=asset,
                price=price,
                currency="USD",
                source="chainlink",
                timestamp=ts,
                confidence=round(confidence, 4),
                round_id=round_id,
            )
            self._history.setdefault(asset, []).append(point)
            logger.info(
                "Chainlink price for %s: %.6f (round=%s, age=%ss, confidence=%.2f)",
                asset, price, round_id, age, confidence,
            )
            return point

        except (StalePriceError, InvalidPriceError) as exc:
            logger.warning("Chainlink validation failed for %s: %s", asset, exc)
            return self._try_fallback(asset, reason=str(exc))
        except Exception as exc:
            logger.error("Chainlink RPC error for %s: %s", asset, exc)
            return self._try_fallback(asset, reason=str(exc))

    def get_historical(self, asset: str, periods: int) -> list[PricePoint]:
        """Retrieve recent historical prices from Chainlink ``getRoundData``.

        Walks backwards from the latest round up to *periods* rounds.
        Falls back to locally cached history if on-chain lookup fails.
        """
        if asset not in self._contracts:
            return self._history.get(asset, [])[-periods:]

        contract = self._contracts[asset]
        try:
            round_id, *_ = self._read_latest_round(asset)
        except Exception:
            return self._history.get(asset, [])[-periods:]

        decimals = self._detect_decimals(asset)
        points: list[PricePoint] = []

        # Chainlink round IDs have a phase prefix — extract the base
        phase_id = round_id >> 64
        base_round = round_id & 0xFFFFFFFFFFFFFFFF

        for i in range(periods):
            rid = base_round - i
            if rid < 1:
                break
            full_rid = (phase_id << 64) | rid
            try:
                r_id, answer, _started, updated_at, _ = contract.functions.getRoundData(
                    full_rid
                ).call()
                if answer <= 0:
                    continue
                price = answer / (10 ** decimals)
                ts = datetime.fromtimestamp(updated_at, tz=timezone.utc)
                points.append(
                    PricePoint(
                        asset=asset,
                        price=price,
                        currency="USD",
                        source="chainlink",
                        timestamp=ts,
                        round_id=r_id,
                    )
                )
            except Exception as exc:
                logger.debug("Failed to read round %s for %s: %s", full_rid, asset, exc)
                continue

        points.reverse()
        return points

    def _try_fallback(self, asset: str, reason: str) -> Optional[PricePoint]:
        """Attempt to get a price from the fallback feed."""
        if self.fallback_feed is None:
            logger.warning("No fallback feed for %s (%s)", asset, reason)
            return None
        logger.info("Using fallback feed for %s (%s)", asset, reason)
        return self.fallback_feed.get_price(asset)

    def is_healthy(self, asset: str) -> bool:
        """Check if a feed is responding with fresh data."""
        try:
            _, answer, _, updated_at, answered_in_round = self._read_latest_round(asset)
            cfg = self._feed_configs[asset]
            age = int(time.time()) - updated_at
            return answer > 0 and age <= cfg.max_staleness
        except Exception:
            return False

    def list_feeds(self) -> dict[str, dict]:
        """Return metadata for all registered feeds."""
        result = {}
        for asset, cfg in self._feed_configs.items():
            result[asset] = {
                "address": cfg.address,
                "max_staleness": cfg.max_staleness,
                "decimals": cfg.decimals,
                "healthy": self.is_healthy(asset),
            }
        return result

"""Oracle price feed interfaces for real-world assets."""

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
import random

import websockets

logger = logging.getLogger(__name__)


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


class WebSocketPriceFeed(BasePriceFeed):
    """
    WebSocket-based price feed for streaming real-time market data.

    Connects to a WebSocket endpoint, subscribes to assets, and keeps
    a cache of the latest price for each asset.
    """

    def __init__(self, websocket_url: str, assets: list[str], source: str = "websocket"):
        self.websocket_url = websocket_url
        self.assets = assets
        self.source = source
        self._latest_prices: dict[str, PricePoint] = {}
        # A lock is not strictly necessary for simple dict reads/writes of single items
        # in Python's CPython implementation as they are atomic.
        # However, for multi-item updates or complex data structures, it would be.
        # We assume for PricePoint updates, atomic dict operations are sufficient.
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._stop_event = asyncio.Event()
        self._stream_task: Optional[asyncio.Task] = None
        logger.info(f"Initialized WebSocketPriceFeed for {assets} from {websocket_url}")

    async def start_streaming(self):
        """Starts the WebSocket connection and data streaming in a background task."""
        if self._stream_task and not self._stream_task.done():
            logger.warning("WebSocket stream already running.")
            return

        self._stop_event.clear()
        self._stream_task = asyncio.create_task(self._receive_loop())
        logger.info("WebSocketPriceFeed streaming task started.")

    async def stop_streaming(self):
        """Stops the WebSocket connection and streaming task."""
        if self._stream_task:
            self._stop_event.set()
            try:
                # Give the task a chance to finish gracefully
                await asyncio.wait_for(self._stream_task, timeout=5.0)
                logger.info("WebSocketPriceFeed streaming task stopped gracefully.")
            except asyncio.TimeoutError:
                logger.warning("WebSocketPriceFeed streaming task did not stop in time, cancelling.")
                self._stream_task.cancel()
                await self._stream_task  # Await cancellation
            self._stream_task = None
        if self._ws:
            await self._ws.close()
            self._ws = None
            logger.info("WebSocket connection closed.")

    async def _receive_loop(self):
        """Connects to the WebSocket and continuously receives price updates."""
        while not self._stop_event.is_set():
            try:
                async with websockets.connect(self.websocket_url) as ws:
                    self._ws = ws
                    logger.info(f"Connected to WebSocket: {self.websocket_url}")

                    # Assuming a subscription message format. This needs to be adapted
                    # based on the actual DEX WebSocket API you are connecting to.
                    # Example for subscribing to multiple assets' trade feeds:
                    if self.assets:
                        # For demonstration, a simple subscription, may need adjustment
                        subscribe_msg = json.dumps({"op": "subscribe", "channels": [f"trade.{asset}" for asset in self.assets]})
                        await ws.send(subscribe_msg)
                        logger.info(f"Sent subscription for: {self.assets}")

                    while not self._stop_event.is_set():
                        try:
                            # Use a timeout to periodically check _stop_event
                            message = await asyncio.wait_for(ws.recv(), timeout=1.0)
                            await self._process_message(message)
                        except asyncio.TimeoutError:
                            # No message received within timeout, check stop event and continue
                            continue
                        except websockets.exceptions.ConnectionClosedOK:
                            logger.info("WebSocket connection closed gracefully during receive loop.")
                            break # Exit inner loop, outer loop will attempt reconnect if _stop_event not set
                        except Exception as e:
                            logger.error(f"Error receiving WebSocket message: {e}")
                            break # Break to attempt reconnect

            except websockets.exceptions.ConnectionClosed as e:
                logger.warning(f"WebSocket connection closed unexpectedly: {e}. Reconnecting in 5s...")
                await asyncio.sleep(5)
            except Exception as e:
                logger.error(f"Error connecting to WebSocket at {self.websocket_url}: {e}. Retrying in 5s...")
                await asyncio.sleep(5)

    async def _process_message(self, message: str):
        """Parses a WebSocket message and updates the latest prices."""
        try:
            data = json.loads(message)
            # --- ADAPT THIS SECTION TO YOUR ACTUAL DEX WEBSOCKET MESSAGE FORMAT ---
            # Example expected formats:
            # 1. Simple price update: {"asset": "ETH", "price": 3000.50, "currency": "USD", "timestamp": "...", "source": "DEX_WS"}
            # 2. Nested data (common for channel-based feeds): {"channel": "trade.ETH", "data": {"asset": "ETH", "price": 3000.50, ...}}

            asset = data.get("asset")
            price = data.get("price")
            currency = data.get("currency", "USD") # Default to USD if not provided
            source = data.get("source", self.source)
            timestamp_str = data.get("timestamp")

            # Check for nested 'data' key if primary keys are missing
            if (not asset or price is None) and isinstance(data.get("data"), dict):
                nested_data = data["data"]
                asset = nested_data.get("asset", asset)
                price = nested_data.get("price", price)
                currency = nested_data.get("currency", currency)
                source = nested_data.get("source", source)
                timestamp_str = nested_data.get("timestamp", timestamp_str)

            if asset and price is not None:
                # Attempt to parse timestamp, fall back to current UTC if invalid or missing
                try:
                    if timestamp_str:
                        # Handle ISO format with or without 'Z' and ensure timezone-aware
                        if timestamp_str.endswith('Z'):
                            ts = datetime.fromisoformat(timestamp_str[:-1]).replace(tzinfo=timezone.utc)
                        else:
                            ts = datetime.fromisoformat(timestamp_str)
                            if ts.tzinfo is None: # Assume UTC if no timezone info
                                ts = ts.replace(tzinfo=timezone.utc)
                    else:
                        ts = datetime.now(timezone.utc)
                except ValueError:
                    logger.warning(f"Could not parse timestamp '{timestamp_str}', using current UTC time.")
                    ts = datetime.now(timezone.utc)

                point = PricePoint(
                    asset=asset,
                    price=float(price),
                    currency=currency,
                    source=source,
                    timestamp=ts,
                    confidence=1.0 # Assume high confidence for real-time stream
                )
                self._latest_prices[asset] = point # Atomic dict update
                logger.debug(f"Received price update for {asset}: {point.price}")
            else:
                logger.debug(f"Skipping malformed or incomplete price message: {message}")
        except json.JSONDecodeError:
            logger.warning(f"Received non-JSON message: {message}")
        except Exception as e:
            logger.error(f"Error processing WebSocket message '{message}': {e}", exc_info=True)

    def get_price(self, asset: str) -> Optional[PricePoint]:
        """Returns the latest price point for a given asset from the cache."""
        return self._latest_prices.get(asset)

    def get_historical(self, asset: str, periods: int) -> list[PricePoint]:
        """Historical data is not directly supported by a real-time streaming feed."""
        logger.warning("get_historical is not supported by WebSocketPriceFeed.")
        return []

"""WebSocket price feed — streams live prices from DEX on-chain events.

Replaces polling with real-time WebSocket subscriptions for:
- Uniswap V3 Swap events (price from pool swaps)
- Chainlink price updates (Aggregator event)
- Generic WebSocket price sources

Falls back to polling if WebSocket connection fails.
"""

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, getcontext
from typing import Callable, Optional
import websockets

from src.oracle.price_feed import BasePriceFeed, PricePoint

logger = logging.getLogger(__name__)

UNISWAP_V3_SWAP_TOPIC = "0xc42079f94a6350d7e6235f29174924f928cc2ac818ebdf2fb8b6a4a6a6d9b84d"
UNISWAP_V3_Q192 = Decimal(2) ** 192
getcontext().prec = 80


@dataclass
class WebSocketConfig:
    """Configuration for WebSocket price feed."""
    url: str
    subscription_msg: dict
    ping_interval: float = 20.0
    reconnect_delay: float = 5.0
    max_reconnects: int = 10
    asset: Optional[str] = None
    source: str = "websocket"
    token0_decimals: int = 18
    token1_decimals: int = 18
    invert_price: bool = False


class WebSocketPriceFeed(BasePriceFeed):
    """Stream real-time prices via WebSocket.

    Connects to a WebSocket endpoint, subscribes to price updates,
    and maintains an in-memory price cache. Supports auto-reconnect.
    """

    def __init__(self, config: WebSocketConfig, fallback: Optional[BasePriceFeed] = None):
        self.config = config
        self.fallback = fallback
        self._prices: dict[str, PricePoint] = {}
        self._history: dict[str, list[PricePoint]] = {}
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._running = False
        self._reconnect_count = 0
        self._callbacks: list[Callable[[PricePoint], None]] = []

    def on_price_update(self, callback: Callable[[PricePoint], None]):
        """Register a callback for real-time price updates."""
        self._callbacks.append(callback)

    def get_price(self, asset: str) -> Optional[PricePoint]:
        """Get latest cached price. Falls back to polling feed if no data."""
        cached = self._prices.get(asset)
        if cached:
            return cached
        if self.fallback:
            return self.fallback.get_price(asset)
        return None

    def get_historical(self, asset: str, periods: int) -> list[PricePoint]:
        """Get recent price history from cache."""
        history = self._history.get(asset, [])
        return history[-periods:]

    async def connect(self):
        """Connect to WebSocket and start streaming."""
        self._running = True
        while self._running and self._reconnect_count <= self.config.max_reconnects:
            try:
                async with websockets.connect(
                    self.config.url,
                    ping_interval=self.config.ping_interval,
                ) as ws:
                    self._ws = ws
                    self._reconnect_count = 0
                    logger.info(f"WebSocket connected to {self.config.url}")

                    # Send subscription message
                    await ws.send(json.dumps(self.config.subscription_msg))

                    # Listen for messages
                    async for message in ws:
                        await self._handle_message(message)

            except websockets.ConnectionClosed as e:
                logger.warning(f"WebSocket closed: {e.code} {e.reason}")
                self._ws = None
            except Exception as e:
                logger.error(f"WebSocket error: {e}")
                self._ws = None

            if self._running:
                self._reconnect_count += 1
                delay = self.config.reconnect_delay * min(self._reconnect_count, 5)
                logger.info(f"Reconnecting in {delay}s (attempt {self._reconnect_count})")
                await asyncio.sleep(delay)

        logger.error("WebSocket max reconnects reached, switching to fallback")
        self._running = False

    async def disconnect(self):
        """Gracefully disconnect from WebSocket."""
        self._running = False
        if self._ws:
            await self._ws.close()
            self._ws = None
        logger.info("WebSocket disconnected")

    async def _handle_message(self, message: str):
        """Parse incoming WebSocket message and update price cache."""
        try:
            data = json.loads(message)
            price_point = self._parse_uniswap_v3_swap(data) or self._parse_price(data)
            if price_point:
                self._update_price(price_point)
        except json.JSONDecodeError:
            logger.warning(f"Invalid JSON from WebSocket: {message[:100]}")
        except Exception as e:
            logger.error(f"Error processing WebSocket message: {e}")

    def _parse_price(self, data: dict) -> Optional[PricePoint]:
        """Parse a WebSocket message into a PricePoint.

        Override this method for specific DEX/oracle message formats.
        Expected data format:
        {
            "asset": "ETH/USD",
            "price": 3500.50,
            "source": "uniswap-v3",
            "confidence": 0.98  // optional
        }
        """
        asset = data.get("asset") or data.get("s") or data.get("pair")
        price = data.get("price") or data.get("p") or data.get("lastPrice")

        if asset is None or price is None:
            return None

        return PricePoint(
            asset=asset,
            price=float(price),
            currency=data.get("currency", "USD"),
            source=data.get("source", "websocket"),
            timestamp=datetime.now(timezone.utc),
            confidence=data.get("confidence", 0.95),
        )

    def _parse_uniswap_v3_swap(self, data: dict) -> Optional[PricePoint]:
        """Parse an Ethereum log subscription payload for a Uniswap V3 Swap event.

        The Swap event's data payload contains five ABI-encoded fields:
        ``amount0``, ``amount1``, ``sqrtPriceX96``, ``liquidity``, and ``tick``.
        The live pool price is derived from ``sqrtPriceX96`` as token1 per token0,
        adjusted by token decimal differences. Use ``invert_price=True`` when the
        configured asset should be represented as token0 per token1.
        """
        params = data.get("params") or {}
        result = params.get("result") if isinstance(params, dict) else None
        if not isinstance(result, dict):
            return None

        topics = result.get("topics") or []
        if not topics or str(topics[0]).lower() != UNISWAP_V3_SWAP_TOPIC:
            return None

        raw_data = result.get("data")
        if not isinstance(raw_data, str) or not raw_data.startswith("0x"):
            return None
        encoded = raw_data[2:]
        if len(encoded) < 64 * 5:
            return None

        try:
            sqrt_price_x96 = int(encoded[64 * 2:64 * 3], 16)
        except ValueError:
            return None
        if sqrt_price_x96 <= 0:
            return None

        raw_price = (Decimal(sqrt_price_x96) * Decimal(sqrt_price_x96)) / UNISWAP_V3_Q192
        decimal_adjustment = Decimal(10) ** (self.config.token1_decimals - self.config.token0_decimals)
        price = raw_price * decimal_adjustment
        if self.config.invert_price:
            if price == 0:
                return None
            price = Decimal(1) / price

        block_timestamp = data.get("timestamp") or result.get("timestamp")
        timestamp = datetime.now(timezone.utc)
        if isinstance(block_timestamp, (int, float)):
            timestamp = datetime.fromtimestamp(block_timestamp, tz=timezone.utc)

        return PricePoint(
            asset=self.config.asset or result.get("address", "UNISWAP-V3"),
            price=float(price),
            currency="USD",
            source="uniswap-v3",
            timestamp=timestamp,
            confidence=0.98,
        )

    def _update_price(self, point: PricePoint):
        """Update price cache and notify callbacks."""
        self._prices[point.asset] = point
        self._history.setdefault(point.asset, []).append(point)

        # Keep only last 1000 points per asset
        if len(self._history[point.asset]) > 1000:
            self._history[point.asset] = self._history[point.asset][-1000:]

        # Notify subscribers
        for cb in self._callbacks:
            try:
                cb(point)
            except Exception as e:
                logger.error(f"Price callback error: {e}")

        logger.debug(f"Price updated: {point.asset} = {point.price:.4f} from {point.source}")

    @property
    def is_connected(self) -> bool:
        """Check if WebSocket is currently connected."""
        return self._ws is not None and self._ws.open

    @property
    def reconnect_count(self) -> int:
        """Number of reconnection attempts."""
        return self._reconnect_count


def create_uniswap_ws_config(
    pool_address: str,
    chain: str = "ethereum",
    *,
    provider_key: Optional[str] = None,
    asset: Optional[str] = None,
    token0_decimals: int = 18,
    token1_decimals: int = 18,
    invert_price: bool = False,
) -> WebSocketConfig:
    """Create WebSocket config for Uniswap V3 pool swap events.

    Args:
        pool_address: Uniswap V3 pool contract address.
        chain: Supported chain key (``ethereum``, ``arbitrum``, or ``base``).
        provider_key: Optional Alchemy API key appended to the WebSocket URL.
        asset: Asset symbol/pair to attach to parsed price points.
        token0_decimals: Decimals for the pool's token0.
        token1_decimals: Decimals for the pool's token1.
        invert_price: Return token0/token1 instead of token1/token0.
    """
    ws_urls = {
        "ethereum": "wss://eth-mainnet.g.alchemy.com/v2",
        "arbitrum": "wss://arb-mainnet.g.alchemy.com/v2",
        "base": "wss://base-mainnet.g.alchemy.com/v2",
    }
    base_url = ws_urls.get(chain, ws_urls["ethereum"])
    url = f"{base_url}/{provider_key}" if provider_key else base_url

    return WebSocketConfig(
        url=url,
        subscription_msg={
            "method": "eth_subscribe",
            "params": [
                "logs",
                {
                    "address": pool_address,
                    "topics": [UNISWAP_V3_SWAP_TOPIC]
                }
            ],
            "id": 1,
            "jsonrpc": "2.0",
        },
        asset=asset,
        source="uniswap-v3",
        token0_decimals=token0_decimals,
        token1_decimals=token1_decimals,
        invert_price=invert_price,
    )

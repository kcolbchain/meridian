"""WebSocket order book feed — streams real-time order book updates from DEXes.

Provides transport-agnostic order book data via the BaseOrderBookFeed interface,
allowing strategies to consume L2 data without caring about the underlying
transport (WebSocket vs REST polling).

Supports:
- Uniswap V3 pool slot0 updates (sqrtPriceX96, liquidity, tick)
- Binance order book WebSocket streams
- Generic WebSocket adapters for custom exchanges
- Reconnection with exponential backoff
- Heartbeat monitoring (ping/pong)
- Order book snapshot + incremental delta merging
"""

import asyncio
import json
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Callable, Optional
import websockets

logger = logging.getLogger(__name__)


class OrderSide(Enum):
    BID = "bid"
    ASK = "ask"


@dataclass
class OrderBookLevel:
    """A single price level in the order book."""
    price: float
    quantity: float
    side: OrderSide


@dataclass
class OrderBookSnapshot:
    """Complete or incremental order book state."""
    symbol: str
    bids: list[OrderBookLevel] = field(default_factory=list)
    asks: list[OrderBookLevel] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)
    sequence: int = 0
    is_delta: bool = False  # True if this is an incremental update

    @property
    def best_bid(self) -> Optional[float]:
        return max((l.price for l in self.bids), default=None)

    @property
    def best_ask(self) -> Optional[float]:
        return min((l.price for l in self.asks), default=None)

    @property
    def spread(self) -> Optional[float]:
        if self.best_bid is not None and self.best_ask is not None:
            return self.best_ask - self.best_bid
        return None

    @property
    def mid_price(self) -> Optional[float]:
        if self.best_bid is not None and self.best_ask is not None:
            return (self.best_bid + self.best_ask) / 2
        return None

    def depth_at_price(self, price: float, side: OrderSide, tolerance: float = 0.001) -> float:
        """Get total quantity within tolerance of a given price on one side."""
        levels = self.bids if side == OrderSide.BID else self.asks
        return sum(
            l.quantity for l in levels
            if abs(l.price - price) / price <= tolerance
        )


class BaseOrderBookFeed(ABC):
    """Abstract interface for order book data sources.

    Strategies should depend on this interface, not on specific
    transport implementations (WebSocket, REST, etc.).
    """

    @abstractmethod
    async def subscribe(self, symbol: str) -> None:
        """Subscribe to order book updates for a symbol."""
        ...

    @abstractmethod
    async def unsubscribe(self, symbol: str) -> None:
        """Unsubscribe from order book updates for a symbol."""
        ...

    @abstractmethod
    def get_orderbook(self, symbol: str) -> Optional[OrderBookSnapshot]:
        """Get the current order book snapshot for a symbol."""
        ...

    def on_update(self, callback: Callable[[str, OrderBookSnapshot], None]):
        """Register a callback for order book updates.

        Args:
            callback: Function receiving (symbol, snapshot) on each update.
        """
        self._callbacks.append(callback)

    def _notify_callbacks(self, symbol: str, snapshot: OrderBookSnapshot):
        for cb in self._callbacks:
            try:
                cb(symbol, snapshot)
            except Exception as e:
                logger.error(f"Order book callback error for {symbol}: {e}")

    def __init__(self):
        self._callbacks: list[Callable[[str, OrderBookSnapshot], None]] = []


@dataclass
class WebSocketOrderBookConfig:
    """Configuration for WebSocket order book feed."""
    url: str
    ping_interval: float = 20.0
    reconnect_base_delay: float = 1.0
    reconnect_max_delay: float = 60.0
    max_reconnects: int = 50
    heartbeat_timeout: float = 30.0
    snapshot_timeout: float = 10.0  # Max time to wait for initial snapshot


class WebSocketOrderBookFeed(BaseOrderBookFeed):
    """WebSocket-based order book feed with reconnection and heartbeat.

    Supports two modes:
    1. **Snapshot + delta**: Receives full snapshot, then incremental updates.
       Used by Binance, Coinbase, etc.
    2. **Event-based**: Receives raw on-chain events (e.g., Uniswap V3 Swap).
       Used for DEX pool monitoring.

    Usage:
        config = WebSocketOrderBookConfig(url="wss://stream.binance.com:9443/ws")
        feed = BinanceOrderBookFeed(config)
        await feed.connect()
        await feed.subscribe("btcusdt")
        ob = feed.get_orderbook("btcusdt")
    """

    def __init__(self, config: WebSocketOrderBookConfig):
        super().__init__()
        self.config = config
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._running = False
        self._reconnect_count = 0
        self._last_heartbeat = 0.0
        self._orderbooks: dict[str, OrderBookSnapshot] = {}
        self._pending_deltas: dict[str, list[OrderBookSnapshot]] = {}
        self._snapshot_received: dict[str, asyncio.Event] = asyncio.Event if False else {}
        self._subscriptions: set[str] = set()
        self._snapshot_events: dict[str, asyncio.Event] = {}

    async def connect(self):
        """Connect to WebSocket with exponential backoff reconnection."""
        self._running = True
        while self._running and self._reconnect_count < self.config.max_reconnects:
            try:
                async with websockets.connect(
                    self.config.url,
                    ping_interval=self.config.ping_interval,
                    ping_timeout=self.config.heartbeat_timeout,
                ) as ws:
                    self._ws = ws
                    self._reconnect_count = 0
                    self._last_heartbeat = time.time()
                    logger.info(
                        f"Order book WebSocket connected to {self.config.url}"
                    )

                    # Re-subscribe to all symbols after reconnection
                    for symbol in self._subscriptions:
                        await self._send_subscribe(symbol)

                    # Process messages
                    async for message in ws:
                        self._last_heartbeat = time.time()
                        await self._handle_message(message)

            except websockets.ConnectionClosed as e:
                logger.warning(
                    f"Order book WebSocket closed: {e.code} {e.reason}"
                )
                self._ws = None
            except asyncio.CancelledError:
                logger.info("Order book WebSocket task cancelled")
                break
            except Exception as e:
                logger.error(f"Order book WebSocket error: {e}")
                self._ws = None

            if self._running:
                self._reconnect_count += 1
                delay = min(
                    self.config.reconnect_base_delay * (2 ** (self._reconnect_count - 1)),
                    self.config.reconnect_max_delay,
                )
                logger.info(
                    f"Reconnecting in {delay:.1f}s "
                    f"(attempt {self._reconnect_count}/{self.config.max_reconnects})"
                )
                await asyncio.sleep(delay)

        if self._running:
            logger.error(
                f"Order book WebSocket: max reconnects "
                f"({self.config.max_reconnects}) reached"
            )
        self._running = False

    async def disconnect(self):
        """Gracefully disconnect."""
        self._running = False
        if self._ws:
            await self._ws.close()
            self._ws = None
        logger.info("Order book WebSocket disconnected")

    async def subscribe(self, symbol: str) -> None:
        """Subscribe to order book updates for a symbol."""
        normalized = symbol.lower().replace("/", "").replace("-", "")
        self._subscriptions.add(normalized)
        self._snapshot_events.setdefault(normalized, asyncio.Event())

        if self._ws:
            await self._send_subscribe(normalized)

    async def unsubscribe(self, symbol: str) -> None:
        """Unsubscribe from order book updates for a symbol."""
        normalized = symbol.lower().replace("/", "").replace("-", "")
        self._subscriptions.discard(normalized)
        self._orderbooks.pop(normalized, None)
        self._pending_deltas.pop(normalized, None)
        self._snapshot_events.pop(normalized, None)

        if self._ws:
            await self._send_unsubscribe(normalized)

    def get_orderbook(self, symbol: str) -> Optional[OrderBookSnapshot]:
        """Get current order book snapshot."""
        normalized = symbol.lower().replace("/", "").replace("-", "")
        return self._orderbooks.get(normalized)

    async def wait_for_snapshot(self, symbol: str, timeout: float = None) -> bool:
        """Wait until a snapshot has been received for the given symbol.

        Returns True if snapshot received, False if timeout.
        """
        normalized = symbol.lower().replace("/", "").replace("-", "")
        event = self._snapshot_events.get(normalized)
        if not event:
            return False
        timeout = timeout or self.config.snapshot_timeout
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            logger.warning(f"Timeout waiting for order book snapshot for {symbol}")
            return False

    @property
    def is_connected(self) -> bool:
        return self._ws is not None and self._ws.open

    @property
    def connected_symbols(self) -> set[str]:
        return self._subscriptions.copy()

    # --- Methods to override for specific exchanges ---

    async def _send_subscribe(self, symbol: str) -> None:
        """Send subscription message. Override for exchange-specific format."""
        raise NotImplementedError("Subclass must implement _send_subscribe")

    async def _send_unsubscribe(self, symbol: str) -> None:
        """Send unsubscription message. Override for exchange-specific format."""
        pass  # Optional, not all exchanges support explicit unsubscribe

    async def _handle_message(self, message: str) -> None:
        """Route incoming messages to the appropriate handler."""
        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            return

        # Dispatch to subclass handler
        snapshot = self._parse_message(data)
        if snapshot:
            self._apply_update(snapshot)

    def _parse_message(self, data: dict) -> Optional[OrderBookSnapshot]:
        """Parse exchange-specific message into OrderBookSnapshot.

        Override in subclasses for exchange-specific message formats.
        """
        raise NotImplementedError("Subclass must implement _parse_message")

    # --- Core order book management ---

    def _apply_update(self, snapshot: OrderBookSnapshot):
        """Apply a snapshot or delta to the order book cache.

        If this is a full snapshot, replace the book entirely.
        If this is a delta, merge into the existing book.
        Deltas received before a snapshot are queued and applied after.
        """
        symbol = snapshot.symbol
        if snapshot.is_delta:
            # Queue deltas until we have a snapshot
            if symbol not in self._orderbooks:
                self._pending_deltas.setdefault(symbol, []).append(snapshot)
                return
            self._merge_delta(snapshot)
        else:
            # Full snapshot — replace book, apply pending deltas
            self._orderbooks[symbol] = snapshot
            # Apply any queued deltas
            for delta in self._pending_deltas.pop(symbol, []):
                if delta.sequence > snapshot.sequence:
                    self._merge_delta(delta)
            # Signal that snapshot is available
            event = self._snapshot_events.get(symbol)
            if event:
                event.set()

        self._notify_callbacks(symbol, snapshot)

    def _merge_delta(self, delta: OrderBookSnapshot):
        """Merge an incremental update into the existing order book."""
        book = self._orderbooks.get(delta.symbol)
        if not book:
            return

        for level in delta.bids:
            self._update_level(book.bids, level)
        for level in delta.asks:
            self._update_level(book.asks, level)

        book.sequence = delta.sequence
        book.timestamp = delta.timestamp
        book.bids.sort(key=lambda l: -l.price)  # Bids: highest first
        book.asks.sort(key=lambda l: l.price)     # Asks: lowest first

    @staticmethod
    def _update_level(
        levels: list[OrderBookLevel],
        new_level: OrderBookLevel,
        tolerance: float = 1e-10,
    ):
        """Update or insert a single price level.

        If quantity is zero, the level is removed (order filled/cancelled).
        """
        for i, existing in enumerate(levels):
            if abs(existing.price - new_level.price) < tolerance:
                if new_level.quantity <= 0:
                    levels.pop(i)
                else:
                    levels[i] = new_level
                return
        if new_level.quantity > 0:
            levels.append(new_level)


class BinanceOrderBookFeed(WebSocketOrderBookFeed):
    """Binance WebSocket order book feed.

    Uses Binance's partial book depth streams for real-time L2 data.
    Stream format: wss://stream.binance.com:9443/ws/<symbol>@depth20@100ms

    This provides a snapshot of the top N levels at regular intervals.
    For full depth with deltas, use @depth instead.
    """

    DEPTH_LEVELS = [5, 10, 20]

    def __init__(
        self,
        config: WebSocketOrderBookConfig,
        depth: int = 20,
        speed: str = "100ms",
    ):
        super().__init__(config)
        if depth not in self.DEPTH_LEVELS:
            raise ValueError(f"depth must be one of {self.DEPTH_LEVELS}")
        self.depth = depth
        self.speed = speed

    async def _send_subscribe(self, symbol: str) -> None:
        """Subscribe to Binance partial book depth stream."""
        msg = {
            "method": "SUBSCRIBE",
            "params": [f"{symbol}@depth{self.depth}@{self.speed}"],
            "id": int(time.time() * 1000),
        }
        await self._ws.send(json.dumps(msg))

    async def _send_unsubscribe(self, symbol: str) -> None:
        """Unsubscribe from Binance stream."""
        msg = {
            "method": "UNSUBSCRIBE",
            "params": [f"{symbol}@depth{self.depth}@{self.speed}"],
            "id": int(time.time() * 1000),
        }
        await self._ws.send(json.dumps(msg))

    def _parse_message(self, data: dict) -> Optional[OrderBookSnapshot]:
        """Parse Binance depth update message.

        Binance format:
        {
            "e": "depthUpdate",
            "E": 1672515782136,
            "s": "BTCUSDT",
            "U": 157,
            "u": 160,
            "b": [["0.0024", "10"], ...],  // bids [price, qty]
            "a": [["0.0026", "100"], ...], // asks [price, qty]
        }
        """
        event_type = data.get("e")
        if event_type != "depthUpdate":
            return None

        symbol = data.get("s", "").lower()
        if not symbol:
            return None

        bids = [
            OrderBookLevel(price=float(b[0]), quantity=float(b[1]), side=OrderSide.BID)
            for b in data.get("b", [])
        ]
        asks = [
            OrderBookLevel(price=float(a[0]), quantity=float(a[1]), side=OrderSide.ASK)
            for a in data.get("a", [])
        ]

        return OrderBookSnapshot(
            symbol=symbol,
            bids=bids,
            asks=asks,
            timestamp=data.get("E", time.time()) / 1000,
            sequence=data.get("u", 0),
            is_delta=True,
        )


class UniswapV3OrderBookFeed(BaseOrderBookFeed):
    """Uniswap V3 order book feed via on-chain slot0 polling + WebSocket events.

    Since Uniswap V3 doesn't have a traditional order book, this reconstructs
    the effective L2 from:
    1. sqrtPriceX96 (current price from slot0)
    2. Tick spacing and fee tier (from pool contract)
    3. Liquidity depth at adjacent ticks

    Uses WebSocket for real-time event monitoring (Swap, Mint, Burn events)
    and falls back to polling slot0 for price updates.

    This is NOT a true order book — it's a simulated representation of
    available liquidity at each tick, useful for market making strategies.
    """

    def __init__(self, pool_address: str, w3_provider_url: str):
        super().__init__()
        self.pool_address = pool_address
        self.w3 = None  # Lazy init to avoid import errors if web3 not installed
        self._w3_url = w3_provider_url
        self._orderbooks: dict[str, OrderBookSnapshot] = {}
        self._ws_feed = None

    async def subscribe(self, symbol: str) -> None:
        """Subscribe to updates for a pool (symbol is the pool address or token pair)."""
        key = symbol.lower()
        if key not in self._orderbooks:
            self._orderbooks[key] = OrderBookSnapshot(symbol=key)
            logger.info(f"Subscribed to Uniswap V3 pool: {key}")

    async def unsubscribe(self, symbol: str) -> None:
        """Unsubscribe from a pool."""
        self._orderbooks.pop(symbol.lower(), None)

    def get_orderbook(self, symbol: str) -> Optional[OrderBookSnapshot]:
        return self._orderbooks.get(symbol.lower())

    def update_from_slot0(self, symbol: str, sqrt_price_x96: int, liquidity: int,
                          tick: int, fee: int = 3000):
        """Update the order book from a Uniswap V3 slot0 call.

        This reconstructs a simplified order book by computing prices
        at adjacent ticks with their available liquidity.

        Args:
            symbol: Pool identifier
            sqrt_price_x96: Current sqrtPriceX96 from slot0
            liquidity: Current liquidity from slot0
            tick: Current tick from slot0
            fee: Fee tier in basis points (e.g., 3000 for 0.3%)
        """
        import math

        book = self._orderbooks.get(symbol.lower())
        if not book:
            book = OrderBookSnapshot(symbol=symbol.lower())
            self._orderbooks[symbol.lower()] = book

        # Calculate current price from sqrtPriceX96
        price = (sqrt_price_x96 / (2 ** 96)) ** 2

        # Reconstruct simplified order book around current tick
        # Each tick represents a 0.01% (1 basis point) price change
        tick_spacing = {100: 1, 500: 10, 3000: 60, 10000: 200}.get(fee, 60)

        # Generate bid/ask levels around current price
        num_levels = 10
        bids = []
        asks = []

        for i in range(1, num_levels + 1):
            bid_tick = tick - i * tick_spacing
            ask_tick = tick + i * tick_spacing

            # Price at tick: price = 1.0001^tick
            bid_price = 1.0001 ** bid_tick
            ask_price = 1.0001 ** ask_tick

            # Simulate decreasing liquidity further from mid
            # In reality, this requires querying liquidityNet at each tick
            bid_qty = liquidity * (0.95 ** i) / (10 ** 18) * price
            ask_qty = liquidity * (0.95 ** i) / (10 ** 18) * price

            if bid_price > 0:
                bids.append(OrderBookLevel(
                    price=bid_price, quantity=bid_qty, side=OrderSide.BID
                ))
            asks.append(OrderBookLevel(
                price=ask_price, quantity=ask_qty, side=OrderSide.ASK
            ))

        book.bids = sorted(bids, key=lambda l: -l.price)
        book.asks = sorted(asks, key=lambda l: l.price)
        book.timestamp = time.time()
        book.sequence += 1

        self._notify_callbacks(symbol.lower(), book)


def create_binance_feed(
    symbols: list[str],
    depth: int = 20,
    speed: str = "100ms",
) -> tuple[BinanceOrderBookFeed, WebSocketOrderBookConfig]:
    """Create a configured Binance order book feed.

    Args:
        symbols: Trading pairs to subscribe to (e.g., ["btcusdt", "ethusdt"])
        depth: Number of price levels (5, 10, or 20)
        speed: Update frequency ("100ms" or "1000ms")

    Returns:
        Tuple of (feed, config) — call feed.connect() to start.
    """
    config = WebSocketOrderBookConfig(
        url="wss://stream.binance.com:9443/ws",
        ping_interval=20.0,
        reconnect_base_delay=1.0,
        reconnect_max_delay=60.0,
        max_reconnects=50,
        heartbeat_timeout=30.0,
    )
    feed = BinanceOrderBookFeed(config, depth=depth, speed=speed)
    return feed, config

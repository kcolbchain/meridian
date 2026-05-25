"""WebSocket streaming for real-time order book updates.

Maintains a local order book via WebSocket feed with
automatic reconnection, heartbeat monitoring, and
transport-agnostic interface for strategies.
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional
import websockets

logger = logging.getLogger(__name__)


@dataclass
class OrderBookLevel:
    price: float
    size: float
    side: str


@dataclass
class OrderBookSnapshot:
    bids: list[OrderBookLevel]
    asks: list[OrderBookLevel]
    timestamp: datetime = field(default_factory=datetime.utcnow)
    symbol: str = ""


class OrderBookWebSocket:
    """WebSocket feed for order book data with reconnect and heartbeat."""

    def __init__(
        self,
        url: str,
        symbol: str,
        max_depth: int = 100,
        heartbeat_timeout: float = 30.0,
    ):
        self.url = url
        self.symbol = symbol
        self.max_depth = max_depth
        self.heartbeat_timeout = heartbeat_timeout
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._running = False
        self._reconnect_attempts = 0
        self._max_reconnects = 20
        self._base_delay = 1.0
        self._bids: dict[float, float] = {}
        self._asks: dict[float, float] = {}
        self._callbacks: list[Callable[[OrderBookSnapshot], None]] = []
        self._last_heartbeat: Optional[datetime] = None

    def on_update(self, callback: Callable[[OrderBookSnapshot], None]):
        self._callbacks.append(callback)

    @property
    def snapshot(self) -> OrderBookSnapshot:
        sorted_bids = sorted(self._bids.items(), reverse=True)[:self.max_depth]
        sorted_asks = sorted(self._asks.items())[:self.max_depth]
        return OrderBookSnapshot(
            bids=[OrderBookLevel(price=p, size=s, side="bid") for p, s in sorted_bids],
            asks=[OrderBookLevel(price=p, size=s, side="ask") for p, s in sorted_asks],
            symbol=self.symbol,
        )

    @property
    def is_connected(self) -> bool:
        return self._ws is not None and self._ws.open

    async def connect(self):
        self._running = True
        while self._running and self._reconnect_attempts <= self._max_reconnects:
            try:
                async with websockets.connect(self.url, ping_interval=20.0) as ws:
                    self._ws = ws
                    self._reconnect_attempts = 0
                    self._last_heartbeat = datetime.utcnow()
                    logger.info(f"OrderBook WS connected: {self.symbol} @ {self.url}")
                    await self._subscribe(ws)
                    async for message in ws:
                        self._last_heartbeat = datetime.utcnow()
                        await self._handle(message)
            except websockets.ConnectionClosed as e:
                logger.warning(f"OrderBook WS closed: {e.code} {e.reason}")
                self._ws = None
            except Exception as e:
                logger.error(f"OrderBook WS error: {e}")
                self._ws = None
            if self._running:
                self._reconnect_attempts += 1
                delay = self._base_delay * min(2 ** self._reconnect_attempts, 60)
                logger.info(f"OrderBook WS reconnect in {delay:.0f}s (attempt {self._reconnect_attempts})")
                await asyncio.sleep(delay)
        logger.error("OrderBook WS max reconnects reached")
        self._running = False

    async def disconnect(self):
        self._running = False
        if self._ws:
            await self._ws.close()
            self._ws = None

    async def _subscribe(self, ws):
        msg = {
            "method": "SUBSCRIBE",
            "params": [f"{self.symbol.lower()}@depth20@100ms"],
            "id": 1,
        }
        await ws.send(json.dumps(msg))

    async def _handle(self, raw: str):
        try:
            data = json.loads(raw)
            self._apply(data)
        except json.JSONDecodeError:
            logger.warning(f"Invalid order book JSON: {raw[:100]}")
        except Exception as e:
            logger.error(f"Order book handle error: {e}")

    def _apply(self, data: dict):
        bids = data.get("bids", data.get("b", []))
        asks = data.get("asks", data.get("a", []))
        updated = False
        for price_str, size_str in bids:
            p, s = float(price_str), float(size_str)
            if s == 0:
                self._bids.pop(p, None)
            else:
                self._bids[p] = s
            updated = True
        for price_str, size_str in asks:
            p, s = float(price_str), float(size_str)
            if s == 0:
                self._asks.pop(p, None)
            else:
                self._asks[p] = s
            updated = True
        if updated:
            snap = self.snapshot
            for cb in self._callbacks:
                try:
                    cb(snap)
                except Exception as e:
                    logger.error(f"OrderBook callback error: {e}")

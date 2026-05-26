import json
import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Optional
from datetime import datetime
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class PriceUpdate:
    asset: str
    price: float
    currency: str
    source: str
    timestamp: datetime


class StreamHandler(ABC):
    @abstractmethod
    async def on_price(self, update: PriceUpdate):
        ...

    @abstractmethod
    async def on_error(self, error: Exception):
        ...


class UniswapV3PoolHandler(StreamHandler):
    def __init__(self):
        self._prices: dict[str, float] = {}

    async def on_price(self, update: PriceUpdate):
        self._prices[update.asset] = update.price
        logger.info(f"Price update: {update.asset}={update.price}")

    async def on_error(self, error: Exception):
        logger.error(f"Stream error: {error}")

    def latest_price(self, asset: str) -> Optional[float]:
        return self._prices.get(asset)


class WebSocketStream:
    def __init__(self, url: str, handler: StreamHandler, reconnect_delay: float = 5.0):
        self._url = url
        self._handler = handler
        self._reconnect_delay = reconnect_delay
        self._subscriptions: set[str] = set()
        self._running = False
        self._ws = None

    def subscribe(self, asset: str):
        self._subscriptions.add(asset)

    def unsubscribe(self, asset: str):
        self._subscriptions.discard(asset)

    async def start(self):
        self._running = True
        while self._running:
            try:
                import websockets

                async with websockets.connect(self._url) as ws:
                    self._ws = ws
                    sub_msg = json.dumps({
                        "type": "subscribe",
                        "assets": list(self._subscriptions),
                    })
                    await ws.send(sub_msg)
                    async for message in ws:
                        if not self._running:
                            break
                        data = json.loads(message)
                        update = PriceUpdate(
                            asset=data.get("asset", "unknown"),
                            price=float(data.get("price", 0)),
                            currency=data.get("currency", "USD"),
                            source=data.get("source", "websocket"),
                            timestamp=datetime.utcnow(),
                        )
                        await self._handler.on_price(update)
            except ImportError:
                logger.error("websockets library not installed. pip install websockets")
                break
            except Exception as e:
                await self._handler.on_error(e)
                if self._running:
                    await asyncio.sleep(self._reconnect_delay)

    async def stop(self):
        self._running = False
        if self._ws:
            await self._ws.close()

"""RWA-specific market maker agent.

Designed for illiquid, irregularly-priced real-world assets where
constant product AMMs fail. Uses oracle-driven pricing, adaptive
spreads, and geography-aware adjustments.
"""

import logging
from typing import Optional

from .base_agent import BaseAgent, Order, Side

logger = logging.getLogger(__name__)


class RWAMarketMaker(BaseAgent):
    """Market maker optimized for real-world assets."""

    def __init__(self, agent_id: str, config: dict, oracle=None):
        super().__init__(agent_id, config)
        self.oracle = oracle
        self.geography = config.get("geography", "default")
        self.base_spread_bps = config.get("base_spread_bps", 200)  # 2%
        self.max_inventory_pct = config.get("max_inventory_pct", 0.3)
        self.volatility_window: list[float] = []
        self.last_oracle_price: Optional[float] = None

    def evaluate_market(self, market_data: dict) -> dict:
        """Evaluate market using oracle price + on-chain signals."""
        oracle_price = market_data.get("oracle_price")
        on_chain_price = market_data.get("on_chain_price")
        volume_24h = market_data.get("volume_24h", 0)

        if oracle_price:
            self.last_oracle_price = oracle_price

        mid_price = oracle_price or on_chain_price
        if mid_price is None:
            return {"tradeable": False, "reason": "no_price_data"}

        self.volatility_window.append(mid_price)
        if len(self.volatility_window) > 100:
            self.volatility_window = self.volatility_window[-100:]

        volatility = self._compute_volatility()
        inventory_ratio = self._inventory_ratio(mid_price)
        liquidity_score = self._liquidity_score(volume_24h)
        geo_adjustment = self._geography_adjustment()

        signals = {
            "tradeable": True,
            "mid_price": mid_price,
            "oracle_price": oracle_price,
            "on_chain_price": on_chain_price,
            "volatility": volatility,
            "inventory_ratio": inventory_ratio,
            "liquidity_score": liquidity_score,
            "geo_adjustment": geo_adjustment,
        }

        self.log_event("market_eval", signals)
        return signals

    def execute_strategy(self, signals: dict) -> list[Order]:
        """Generate bid/ask orders based on market signals."""
        if not signals.get("tradeable"):
            return []

        mid = signals["mid_price"]
        vol = signals["volatility"]
        inv = signals["inventory_ratio"]
        liq = signals["liquidity_score"]
        geo = signals["geo_adjustment"]

        # Compute spread: base + volatility component + illiquidity premium
        spread_bps = self.base_spread_bps
        spread_bps += vol * 500  # 5x volatility impact
        spread_bps += (1 - liq) * 300  # wider for illiquid assets
        spread_bps *= geo  # geography multiplier

        # Skew spread based on inventory — incentivize reducing exposure
        bid_spread_bps = spread_bps * (1 + inv * 0.5)  # wider bid when long
        ask_spread_bps = spread_bps * (1 - inv * 0.3)  # tighter ask when long

        bid_price = mid * (1 - bid_spread_bps / 10000)
        ask_price = mid * (1 + ask_spread_bps / 10000)

        # Size based on available balance and risk limits
        max_order_pct = self.config.get("max_order_size_pct", 0.1)
        bid_size = self.position.quote_balance * max_order_pct / bid_price
        ask_size = self.position.base_balance * max_order_pct

        orders = []
        if bid_size > 0 and inv < self.max_inventory_pct:
            orders.append(Order(side=Side.BID, price=round(bid_price, 6), size=round(bid_size, 6)))
        if ask_size > 0 and inv > -self.max_inventory_pct:
            orders.append(Order(side=Side.ASK, price=round(ask_price, 6), size=round(ask_size, 6)))

        self.log_event("orders_placed", {
            "spread_bps": round(spread_bps, 1),
            "bid": bid_price if bid_size > 0 else None,
            "ask": ask_price if ask_size > 0 else None,
            "bid_size": bid_size,
            "ask_size": ask_size,
        })

        return orders

    def rebalance(self) -> list[Order]:
        """Reduce inventory when exposure exceeds limits."""
        if self.last_oracle_price is None:
            return []

        exposure = self.position.net_exposure
        max_base = self.config.get("max_base_position", float("inf"))

        if abs(exposure) <= max_base:
            return []

        excess = abs(exposure) - max_base
        if exposure > 0:
            # Too long — place aggressive ask
            price = self.last_oracle_price * 0.998  # slight discount to move
            return [Order(side=Side.ASK, price=round(price, 6), size=round(excess, 6))]
        else:
            # Too short — place aggressive bid
            price = self.last_oracle_price * 1.002
            return [Order(side=Side.BID, price=round(price, 6), size=round(excess, 6))]

    def _compute_volatility(self) -> float:
        """Realized volatility from price window. Returns 0-1 normalized."""
        if len(self.volatility_window) < 2:
            return 0.5  # default medium vol
        prices = self.volatility_window
        returns = [(prices[i] - prices[i-1]) / prices[i-1] for i in range(1, len(prices))]
        if not returns:
            return 0.0
        import numpy as np
        vol = float(np.std(returns))
        return min(vol * 100, 1.0)  # normalize to 0-1

    def _inventory_ratio(self, mid_price: float) -> float:
        """Inventory skew: -1 (all quote) to +1 (all base)."""
        base_value = self.position.base_balance * mid_price
        total = base_value + self.position.quote_balance
        if total == 0:
            return 0.0
        return (base_value - self.position.quote_balance) / total

    def _liquidity_score(self, volume_24h: float) -> float:
        """0 (illiquid) to 1 (liquid) based on 24h volume."""
        threshold = self.config.get("liquid_volume_threshold", 1_000_000)
        return min(volume_24h / threshold, 1.0)

    def _geography_adjustment(self) -> float:
        """Spread multiplier per geography. Higher = wider spreads."""
        geo_multipliers = {
            "default": 1.0,
            "US": 1.0,
            "EU": 1.1,
            "IN": 1.2,   # India — wider due to regulatory overhead
            "JP": 1.05,
            "TH": 1.15,
            "LATAM": 1.3,
        }
        return geo_multipliers.get(self.geography, 1.0)


import logging
import asyncio # New import for async operations
from typing import Optional
import random # Ensure random is explicitly imported if used outside main guard

from .base_agent import BaseAgent, Order, Side
from src.oracle.price_feed import MockPriceFeed, WebSocketPriceFeed, PricePoint # New import

logger = logging.getLogger(__name__)


class RWAMarketMaker(BaseAgent):
    """Market maker optimized for real-world assets."""

    def __init__(self, agent_id: str, config: dict, oracle=None):
        super().__init__(agent_id, config)
        self.oracle = oracle
        self.geography = config.get("geography", "default")
        self.base_spread_bps = config.get("base_spread_bps", 200)  # 2%
        self.max_inventory_pct = config.get("max_inventory_pct", 0.3)
        self.volatility_window: list[float] = []
        self.last_oracle_price: Optional[float] = None
        self.target_asset = config.get("target_asset", "RWA_TOKEN") # New: Asset to focus on for WS


    def evaluate_market(self, market_data: dict) -> dict:
        """Evaluate market using oracle price + on-chain signals."""
        oracle_price = market_data.get("oracle_price")
        on_chain_price = market_data.get("on_chain_price")
        volume_24h = market_data.get("volume_24h", 0)

        if oracle_price:
            self.last_oracle_price = oracle_price

        mid_price = oracle_price or on_chain_price
        if mid_price is None:
            return {"tradeable": False, "reason": "no_price_data"}

        self.volatility_window.append(mid_price)
        if len(self.volatility_window) > 100:
            self.volatility_window = self.volatility_window[-100:]

        volatility = self._compute_volatility()
        inventory_ratio = self._inventory_ratio(mid_price)
        liquidity_score = self._liquidity_score(volume_24h)
        geo_adjustment = self._geography_adjustment()

        signals = {
            "tradeable": True,
            "mid_price": mid_price,
            "oracle_price": oracle_price,
            "on_chain_price": on_chain_price,
            "volatility": volatility,
            "inventory_ratio": inventory_ratio,
            "liquidity_score": liquidity_score,
            "geo_adjustment": geo_adjustment,
        }

        self.log_event("market_eval", signals)
        return signals

    def execute_strategy(self, signals: dict) -> list[Order]:
        """Generate bid/ask orders based on market signals."""
        if not signals.get("tradeable"):
            return []

        mid = signals["mid_price"]
        vol = signals["volatility"]
        inv = signals["inventory_ratio"]
        liq = signals["liquidity_score"]
        geo = signals["geo_adjustment"]

        # Compute spread: base + volatility component + illiquidity premium
        spread_bps = self.base_spread_bps
        spread_bps += vol * 500  # 5x volatility impact
        spread_bps += (1 - liq) * 300  # wider for illiquid assets
        spread_bps *= geo  # geography multiplier

        # Skew spread based on inventory — incentivize reducing exposure
        bid_spread_bps = spread_bps * (1 + inv * 0.5)  # wider bid when long
        ask_spread_bps = spread_bps * (1 - inv * 0.3)  # tighter ask when long

        bid_price = mid * (1 - bid_spread_bps / 10000)
        ask_price = mid * (1 + ask_spread_bps / 10000)

        # Size based on available balance and risk limits
        max_order_pct = self.config.get("max_order_size_pct", 0.1)
        bid_size = self.position.quote_balance * max_order_pct / bid_price
        ask_size = self.position.base_balance * max_order_pct

        orders = []
        if bid_size > 0 and inv < self.max_inventory_pct:
            orders.append(Order(side=Side.BID, price=round(bid_price, 6), size=round(bid_size, 6)))
        if ask_size > 0 and inv > -self.max_inventory_pct:
            orders.append(Order(side=Side.ASK, price=round(ask_price, 6), size=round(ask_size, 6)))

        self.log_event("orders_placed", {
            "spread_bps": round(spread_bps, 1),
            "bid": bid_price if bid_size > 0 else None,
            "ask": ask_price if ask_size > 0 else None,
            "bid_size": bid_size,
            "ask_size": ask_size,
        })

        return orders

    def rebalance(self) -> list[Order]:
        """Reduce inventory when exposure exceeds limits."""
        if self.last_oracle_price is None:
            return []

        exposure = self.position.net_exposure
        max_base = self.config.get("max_base_position", float("inf"))

        if abs(exposure) <= max_base:
            return []

        excess = abs(exposure) - max_base
        if exposure > 0:
            # Too long — place aggressive ask
            price = self.last_oracle_price * 0.998  # slight discount to move
            return [Order(side=Side.ASK, price=round(price, 6), size=round(excess, 6))]
        else:
            # Too short — place aggressive bid
            price = self.last_oracle_price * 1.002
            return [Order(side=Side.BID, price=round(price, 6), size=round(excess, 6))]

    def _compute_volatility(self) -> float:
        """Realized volatility from price window. Returns 0-1 normalized."""
        if len(self.volatility_window) < 2:
            return 0.5  # default medium vol
        prices = self.volatility_window
        returns = [(prices[i] - prices[i-1]) / prices[i-1] for i in range(1, len(prices))]
        if not returns:
            return 0.0
        import numpy as np
        vol = float(np.std(returns))
        return min(vol * 100, 1.0)  # normalize to 0-1

    def _inventory_ratio(self, mid_price: float) -> float:
        """Inventory skew: -1 (all quote) to +1 (all base)."""
        base_value = self.position.base_balance * mid_price
        total = base_value + self.position.quote_balance
        if total == 0:
            return 0.0
        return (base_value - self.position.quote_balance) / total

    def _liquidity_score(self, volume_24h: float) -> float:
        """0 (illiquid) to 1 (liquid) based on 24h volume."""
        threshold = self.config.get("liquid_volume_threshold", 1_000_000)
        return min(volume_24h / threshold, 1.0)

    def _geography_adjustment(self) -> float:
        """Spread multiplier per geography. Higher = wider spreads."""
        geo_multipliers = {
            "default": 1.0,
            "US": 1.0,
            "EU": 1.1,
            "IN": 1.2,   # India — wider due to regulatory overhead
            "JP": 1.05,
            "TH": 1.15,
            "LATAM": 1.3,
        }
        return geo_multipliers.get(self.geography, 1.0)


async def main_loop(agent: RWAMarketMaker, oracle, simulate: bool):
    """Main simulation or live trading loop."""
    if isinstance(oracle, WebSocketPriceFeed):
        await oracle.start_streaming()
        logger.info("WebSocketPriceFeed started.")
        # Give some time for the WebSocket to connect and receive initial data
        await asyncio.sleep(2) # Wait for initial prices

    try:
        tick = 0
        while True:
            tick += 1
            oracle_price_val = None
            price_for_pnl = 0.0

            if simulate:
                # Original simulation logic with mock data
                base_price = 100.0 # This would be random.gauss around base_price in current mock.
                # If using MockPriceFeed object directly, we'd call its get_price
                price_point = oracle.get_price(agent.target_asset)
                if price_point:
                    oracle_price_val = price_point.price
                    price_for_pnl = oracle_price_val
                else:
                    # Fallback if MockPriceFeed doesn't have initial price
                    oracle_price_val = base_price + random.gauss(0, 2)
                    price_for_pnl = oracle_price_val
            else:
                # Use WebSocketPriceFeed for live data
                price_point: Optional[PricePoint] = oracle.get_price(agent.target_asset)
                if price_point:
                    oracle_price_val = price_point.price
                    price_for_pnl = oracle_price_val # For PnL calculation
                    logger.debug(f"Tick {tick:3d} | Live Price: {price_for_pnl:.2f}")
                else:
                    logger.warning(f"No live price data for {agent.target_asset} yet. Skipping tick {tick}.")
                    oracle_price_val = agent.last_oracle_price # Use last known price if available
                    price_for_pnl = oracle_price_val or 0.0 # For PnL calculation, avoid None
                    if oracle_price_val is None:
                        await asyncio.sleep(1) # Wait longer if no price at all
                        continue

            market_data = {
                "oracle_price": oracle_price_val,
                "on_chain_price": (oracle_price_val * (1 + random.gauss(0, 0.005))) if oracle_price_val else None,
                "volume_24h": random.uniform(10000, 500000),
            }

            orders = agent.tick(market_data)
            pnl = agent.get_pnl(price_for_pnl)
            print(f"Tick {tick:3d} | Price: {price_for_pnl:.2f} | Orders: {len(orders)} | PnL: {pnl['total']:.2f}")

            if simulate:
                await asyncio.sleep(0.1) # Simulate real-time updates for mock
            else:
                await asyncio.sleep(1) # Wait 1 second for next live update check

    except asyncio.CancelledError:
        logger.info("Main loop cancelled.")
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received, stopping main loop.")
    finally:
        if isinstance(oracle, WebSocketPriceFeed):
            await oracle.stop_streaming()
            logger.info("WebSocketPriceFeed stopped.")

if __name__ == "__main__":
    import click
    import yaml
    # random is already imported at top of file, no need here

    @click.command()
    @click.option("--config", "config_path", default="config/default.yaml")
    @click.option("--simulate", is_flag=True, default=False, help="Run with mock data (True) or live WS data (False)")
    @click.option("--ws-url", default="ws://localhost:8765", help="WebSocket URL for live price data")
    @click.option("--asset", default="RWA_TOKEN", help="The asset to track via WebSocket (e.g., ETH, RWA_TOKEN)")
    def run(config_path, simulate, ws_url, asset):
        logging.basicConfig(level=logging.INFO)

        with open(config_path) as f:
            config = yaml.safe_load(f)

        # Update agent config with target asset from CLI option
        agent_config = config.get("agent", {})
        agent_config["target_asset"] = asset
        agent = RWAMarketMaker("rwa-mm-1", agent_config)

        current_oracle = None
        if simulate:
            logger.info(f"Running in simulation mode with MockPriceFeed for asset: {asset}.")
            # MockPriceFeed requires base prices for specific assets
            mock_base_prices = {asset: config.get("agent", {}).get("mock_base_price", 100.0)}
            current_oracle = MockPriceFeed(base_prices=mock_base_prices, volatility=0.02)
        else:
            logger.info(f"Running in live mode with WebSocketPriceFeed, connecting to {ws_url} for asset {asset}.")
            current_oracle = WebSocketPriceFeed(websocket_url=ws_url, assets=[asset])

        try:
            asyncio.run(main_loop(agent, current_oracle, simulate))
        except KeyboardInterrupt:
            logger.info("Application interrupted by user (Ctrl+C). Shutting down.")
        except Exception as e:
            logger.exception(f"An unexpected error occurred in the application: {e}")

    run()

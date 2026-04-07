import math
from dataclasses import dataclass
from typing import Dict, Any

from src.agents.strategy import Strategy

@dataclass
class AvellanedaStoikovConfig:
    """Configuration for the Avellaneda-Stoikov market-making strategy."""
    gamma: float = 0.1             # Risk aversion parameter (gamma > 0)
    sigma: float = 0.01            # Volatility estimate (standard deviation of log returns)
    k: float = 1.0                 # Market order arrival intensity parameter (k > 0)
    time_horizon_seconds: float = 3600.0 # Total trading horizon in seconds (T)
    min_spread: float = 0.0001     # Minimum allowed half-spread to prevent zero or negative spreads
    order_size: float = 1.0        # Default order size for bids/asks
    max_inventory: int = 100       # Maximum absolute inventory allowed (to limit risk)

class AvellanedaStoikovStrategy(Strategy):
    """
    Implements the Avellaneda-Stoikov optimal market-making strategy.

    This strategy aims to maximize the market maker's utility, which balances
    profit from quoted spreads and the cost of holding inventory risk, over a
    given trading horizon. It dynamically adjusts bid and ask quotes based on
    current inventory, time remaining, market volatility, and risk aversion.

    Key Equations (from Avellaneda & Stoikov (2008), Section 3.2):
    - Reservation Price (r_t):
      r_t = S_t - q * gamma * sigma^2 * (T - t)

    - Half-Spread (delta_A):
      delta_A = (1/gamma) * log(1 + gamma / k)

    Where:
    - S_t: Current mid-price
    - q: Current inventory (positive for long, negative for short)
    - gamma: Risk aversion parameter (how much the MM penalizes inventory risk)
    - sigma: Volatility estimate (standard deviation of asset returns)
    - T: Total trading horizon (time until the strategy concludes)
    - t: Current time elapsed since the start of the horizon
    - k: Market order arrival intensity parameter (related to how often market orders arrive)

    Optimal Bid Price: p_b* = r_t - delta_A
    Optimal Ask Price: p_a* = r_t + delta_A

    Note: The time 't' should be interpreted as time elapsed, so `time_remaining = T - t`.
    The `market_data` dictionary is expected to contain at least 'mid_price'.
    """

    def __init__(self, config: AvellanedaStoikovConfig):
        """
        Initializes the Avellaneda-Stoikov strategy with the given configuration.

        Args:
            config (AvellanedaStoikovConfig): Configuration object for the strategy.
        """
        super().__init__(config)
        self.config: AvellanedaStoikovConfig = config

        # Validate initial configuration parameters
        if not (self.config.gamma > 0 and self.config.sigma >= 0 and self.config.k > 0 and self.config.time_horizon_seconds > 0):
            raise ValueError("Config parameters gamma, sigma, k, and time_horizon_seconds must be positive (sigma can be zero for zero volatility).")
        if self.config.min_spread < 0:
            raise ValueError("min_spread cannot be negative.")
        if self.config.order_size <= 0:
            raise ValueError("order_size must be positive.")
        if self.config.max_inventory < 0:
            raise ValueError("max_inventory cannot be negative.")


    def generate_orders(self, market_data: Dict[str, Any], current_inventory: float, current_time: float) -> Dict[str, Any]:
        """
        Generates bid and ask prices based on the Avellaneda-Stoikov model.

        Args:
            market_data (Dict[str, Any]): Dictionary containing market information,
                                         e.g., {'mid_price': 100.0}.
            current_inventory (float): Current inventory of the asset.
                                       Positive for long, negative for short.
            current_time (float): Current time in seconds, relative to the start
                                  of the trading horizon (0 to T).

        Returns:
            Dict[str, Any]: A dictionary containing 'bid_price', 'ask_price',
                            'bid_amount', 'ask_amount'.
                            Prices can be None if no order is to be placed.
        """
        mid_price = market_data.get('mid_price')
        if mid_price is None:
            raise ValueError("Market data must contain 'mid_price'.")

        gamma = self.config.gamma
        sigma = self.config.sigma
        k = self.config.k
        T = self.config.time_horizon_seconds
        order_size = self.config.order_size
        min_spread = self.config.min_spread
        max_inventory = self.config.max_inventory

        if current_time < 0:
            raise ValueError("Current time cannot be negative.")

        time_remaining = T - current_time

        # If at or past the end of the trading horizon, liquidate inventory.
        # This simplifies the end-of-period behavior.
        if time_remaining <= 0:
            if abs(current_inventory) > 0:
                # Attempt to liquidate inventory at mid-price +/- min_spread
                bid_price = mid_price - min_spread if current_inventory < 0 else None
                ask_price = mid_price + min_spread if current_inventory > 0 else None
                bid_amount = abs(current_inventory) if bid_price is not None else 0.0
                ask_amount = abs(current_inventory) if ask_price is not None else 0.0
                return {
                    'bid_price': bid_price,
                    'ask_price': ask_price,
                    'bid_amount': bid_amount,
                    'ask_amount': ask_amount
                }
            # No inventory and time expired, so no quotes
            return {'bid_price': None, 'ask_price': None, 'bid_amount': 0.0, 'ask_amount': 0.0}

        # If inventory exceeds max_inventory, only place orders to reduce the excess.
        # This is a risk management override.
        if current_inventory > max_inventory:
            return {'bid_price': None, 'ask_price': mid_price + min_spread, 'bid_amount': 0.0, 'ask_amount': current_inventory - max_inventory}
        elif current_inventory < -max_inventory:
            return {'bid_price': mid_price - min_spread, 'ask_price': None, 'bid_amount': abs(current_inventory) - max_inventory, 'ask_amount': 0.0}

        # 1. Calculate the reservation price adjustment due to inventory risk
        # This term shifts the mid-price based on inventory and risk aversion.
        # A positive inventory (long) reduces the reservation price, encouraging selling.
        # A negative inventory (short) increases the reservation price, encouraging buying.
        inventory_risk_adjustment = current_inventory * gamma * sigma**2 * time_remaining
        reservation_price = mid_price - inventory_risk_adjustment

        # 2. Calculate the optimal half-spread
        # This term is independent of inventory and time remaining in this formulation.
        # It balances the profit from order execution against the probability of order not being filled.
        # Ensure the log argument is positive. Given k > 0 and gamma > 0, 1 + gamma / k will always be > 1.
        optimal_half_spread = (1 / gamma) * math.log(1 + gamma / k)

        # Apply minimum spread requirement to prevent quotes that are too tight
        optimal_half_spread = max(optimal_half_spread, min_spread)

        # Calculate optimal bid and ask prices based on reservation price and half-spread
        bid_price = reservation_price - optimal_half_spread
        ask_price = reservation_price + optimal_half_spread

        return {
            'bid_price': bid_price,
            'ask_price': ask_price,
            'bid_amount': order_size,
            'ask_amount': order_size
        }


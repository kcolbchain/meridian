from abc import ABC, abstractmethod
from typing import Any, Dict

class Strategy(ABC):
    """Base class for all trading strategies.

    Provides an abstract interface that all concrete strategies should implement.
    Strategies are responsible for generating trading orders based on market data,
    current inventory, and time.
    """

    def __init__(self, config: Any):
        """
        Initializes the strategy with a given configuration.

        Args:
            config (Any): A dataclass or dictionary holding strategy-specific parameters.
        """
        self.config = config

    @abstractmethod
    def generate_orders(self, market_data: Dict[str, Any], current_inventory: float, current_time: float) -> Dict[str, Any]:
        """
        Generates trading orders (bid/ask prices and amounts) based on the current market state.

        Args:
            market_data (Dict[str, Any]): A dictionary containing relevant market information,
                                         e.g., {'mid_price': 100.0, 'timestamp': ...}.
            current_inventory (float): The current inventory of the asset the strategy is trading.
                                       Positive for long positions, negative for short positions.
            current_time (float): The current simulation or real-world time, typically in seconds
                                  relative to a starting point or absolute timestamp.

        Returns:
            Dict[str, Any]: A dictionary containing the generated orders.
                            Expected keys: 'bid_price', 'ask_price', 'bid_amount', 'ask_amount'.
                            If a price is None, it implies no order is placed on that side.
                            Amounts should be positive floats.
        """
        pass


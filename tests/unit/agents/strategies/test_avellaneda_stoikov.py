import unittest
import math
from src.agents.strategies.avellaneda_stoikov import AvellanedaStoikovConfig, AvellanedaStoikovStrategy

class TestAvellanedaStoikovStrategy(unittest.TestCase):

    def setUp(self):
        self.default_config = AvellanedaStoikovConfig(
            gamma=0.1,
            sigma=0.01,
            k=1.0,
            time_horizon_seconds=3600.0, # 1 hour
            min_spread=0.0001,
            order_size=1.0,
            max_inventory=100
        )
        self.mid_price = 100.0

    def test_initial_state_zero_inventory(self):
        """Test with zero inventory at the start of the horizon, checking base spread."""
        strategy = AvellanedaStoikovStrategy(self.default_config)
        market_data = {'mid_price': self.mid_price}
        current_inventory = 0.0
        current_time = 0.0

        orders = strategy.generate_orders(market_data, current_inventory, current_time)

        # Expected calculations:
        # time_remaining = 3600 - 0 = 3600
        # inventory_risk_adjustment = 0 * gamma * sigma^2 * time_remaining = 0
        # reservation_price = mid_price - 0 = 100.0
        # optimal_half_spread = (1/gamma) * log(1 + gamma / k)
        expected_half_spread = (1 / self.default_config.gamma) * math.log(1 + self.default_config.gamma / self.default_config.k)
        expected_half_spread = max(expected_half_spread, self.default_config.min_spread) # Apply min_spread

        expected_bid = self.mid_price - expected_half_spread
        expected_ask = self.mid_price + expected_half_spread

        self.assertAlmostEqual(orders['bid_price'], expected_bid, places=8)
        self.assertAlmostEqual(orders['ask_price'], expected_ask, places=8)
        self.assertEqual(orders['bid_amount'], self.default_config.order_size)
        self.assertEqual(orders['ask_amount'], self.default_config.order_size)

    def test_positive_inventory(self):
        """Test with positive inventory, should shift quotes lower to encourage selling."""
        strategy = AvellanedaStoikovStrategy(self.default_config)
        market_data = {'mid_price': self.mid_price}
        current_inventory = 10.0
        current_time = 0.0

        orders = strategy.generate_orders(market_data, current_inventory, current_time)

        # Expected calculations:
        # time_remaining = 3600
        # inventory_risk_adjustment = 10 * 0.1 * (0.01)^2 * 3600 = 0.36
        # reservation_price = 100.0 - 0.36 = 99.64
        # optimal_half_spread (same as before)
        expected_half_spread = (1 / self.default_config.gamma) * math.log(1 + self.default_config.gamma / self.default_config.k)
        expected_half_spread = max(expected_half_spread, self.default_config.min_spread)

        expected_reservation_price = self.mid_price - current_inventory * self.default_config.gamma * \
                                     self.default_config.sigma**2 * (self.default_config.time_horizon_seconds - current_time)
        expected_bid = expected_reservation_price - expected_half_spread
        expected_ask = expected_reservation_price + expected_half_spread

        self.assertAlmostEqual(orders['bid_price'], expected_bid, places=8)
        self.assertAlmostEqual(orders['ask_price'], expected_ask, places=8)
        self.assertLess(orders['bid_price'], self.mid_price)
        self.assertLess(orders['ask_price'], self.mid_price + expected_half_spread * 2) # Overall spread shifted down

    def test_negative_inventory(self):
        """Test with negative inventory, should shift quotes higher to encourage buying."""
        strategy = AvellanedaStoikovStrategy(self.default_config)
        market_data = {'mid_price': self.mid_price}
        current_inventory = -10.0
        current_time = 0.0

        orders = strategy.generate_orders(market_data, current_inventory, current_time)

        # Expected calculations:
        # time_remaining = 3600
        # inventory_risk_adjustment = -10 * 0.1 * (0.01)^2 * 3600 = -0.36
        # reservation_price = 100.0 - (-0.36) = 100.36
        # optimal_half_spread (same as before)
        expected_half_spread = (1 / self.default_config.gamma) * math.log(1 + self.default_config.gamma / self.default_config.k)
        expected_half_spread = max(expected_half_spread, self.default_config.min_spread)

        expected_reservation_price = self.mid_price - current_inventory * self.default_config.gamma * \
                                     self.default_config.sigma**2 * (self.default_config.time_horizon_seconds - current_time)
        expected_bid = expected_reservation_price - expected_half_spread
        expected_ask = expected_reservation_price + expected_half_spread

        self.assertAlmostEqual(orders['bid_price'], expected_bid, places=8)
        self.assertAlmostEqual(orders['ask_price'], expected_ask, places=8)
        self.assertGreater(orders['ask_price'], self.mid_price)
        self.assertGreater(orders['bid_price'], self.mid_price - expected_half_spread * 2) # Overall spread shifted up

    def test_time_decay(self):
        """Test how quotes change as time approaches the end of the horizon, particularly reservation price."""
        config = AvellanedaStoikovConfig(
            gamma=0.1, sigma=0.01, k=1.0, time_horizon_seconds=3600.0, min_spread=0.0001
        )
        strategy = AvellanedaStoikovStrategy(config)
        market_data = {'mid_price': self.mid_price}
        current_inventory = 10.0 # Positive inventory

        # At start (t=0)
        orders_start = strategy.generate_orders(market_data, current_inventory, 0.0)
        
        # Halfway (t=T/2)
        orders_half = strategy.generate_orders(market_data, current_inventory, config.time_horizon_seconds / 2)
        
        # Near end (t=T-epsilon)
        orders_near_end = strategy.generate_orders(market_data, current_inventory, config.time_horizon_seconds - 1.0) # 1 second left

        # Expect inventory risk adjustment to decrease as time_remaining decreases.
        # With q > 0, reservation_price = S_t - q * (term), so (q * term) decreases,
        # which means reservation_price increases (moves closer to mid_price).
        res_price_start = (orders_start['bid_price'] + orders_start['ask_price']) / 2
        res_price_half = (orders_half['bid_price'] + orders_half['ask_price']) / 2
        res_price_near_end = (orders_near_end['bid_price'] + orders_near_end['ask_price']) / 2

        self.assertGreater(res_price_half, res_price_start)
        self.assertGreater(res_price_near_end, res_price_half)
        
        # The half-spread (delta_A) itself does not depend on time (T-t) in this formulation.
        # So the *total spread* (ask - bid) should remain constant (unless min_spread affects it).
        spread_start = orders_start['ask_price'] - orders_start['bid_price']
        spread_half = orders_half['ask_price'] - orders_half['bid_price']
        spread_near_end = orders_near_end['ask_price'] - orders_near_end['bid_price']
        
        self.assertAlmostEqual(spread_start, spread_half, places=8)
        self.assertAlmostEqual(spread_half, spread_near_end, places=8)

    def test_end_of_horizon_positive_inventory(self):
        """Test behavior at the very end of the horizon with positive inventory."""
        config = self.default_config
        strategy = AvellanedaStoikovStrategy(config)
        market_data = {'mid_price': self.mid_price}
        current_inventory = 5.0
        
        orders_end = strategy.generate_orders(market_data, current_inventory, config.time_horizon_seconds)
        self.assertIsNone(orders_end['bid_price'])
        self.assertAlmostEqual(orders_end['ask_price'], self.mid_price + config.min_spread)
        self.assertAlmostEqual(orders_end['ask_amount'], current_inventory)
        self.assertEqual(orders_end['bid_amount'], 0.0) # No bid to liquidate positive inventory

    def test_end_of_horizon_negative_inventory(self):
        """Test behavior at the very end of the horizon with negative inventory."""
        config = self.default_config
        strategy = AvellanedaStoikovStrategy(config)
        market_data = {'mid_price': self.mid_price}
        current_inventory = -5.0
        
        orders_end = strategy.generate_orders(market_data, current_inventory, config.time_horizon_seconds)
        self.assertIsNone(orders_end['ask_price'])
        self.assertAlmostEqual(orders_end['bid_price'], self.mid_price - config.min_spread)
        self.assertAlmostEqual(orders_end['bid_amount'], abs(current_inventory))
        self.assertEqual(orders_end['ask_amount'], 0.0)

    def test_end_of_horizon_zero_inventory(self):
        """Test behavior at the very end of the horizon with zero inventory."""
        config = self.default_config
        strategy = AvellanedaStoikovStrategy(config)
        market_data = {'mid_price': self.mid_price}
        current_inventory = 0.0
        
        orders_end = strategy.generate_orders(market_data, current_inventory, config.time_horizon_seconds)
        self.assertIsNone(orders_end['bid_price'])
        self.assertIsNone(orders_end['ask_price'])
        self.assertEqual(orders_end['bid_amount'], 0.0)
        self.assertEqual(orders_end['ask_amount'], 0.0)

    def test_invalid_market_data(self):
        """Test with missing mid_price in market_data."""
        strategy = AvellanedaStoikovStrategy(self.default_config)
        market_data = {} # Missing mid_price
        current_inventory = 0.0
        current_time = 0.0
        with self.assertRaises(ValueError):
            strategy.generate_orders(market_data, current_inventory, current_time)

    def test_config_validation(self):
        """Test that invalid config parameters raise errors during initialization."""
        with self.assertRaises(ValueError):
            AvellanedaStoikovStrategy(AvellanedaStoikovConfig(gamma=0)) # gamma <= 0
        with self.assertRaises(ValueError):
            AvellanedaStoikovStrategy(AvellanedaStoikovConfig(sigma=-0.01)) # sigma < 0
        with self.assertRaises(ValueError):
            AvellanedaStoikovStrategy(AvellanedaStoikovConfig(k=0)) # k <= 0
        with self.assertRaises(ValueError):
            AvellanedaStoikovStrategy(AvellanedaStoikovConfig(time_horizon_seconds=0)) # T <= 0
        with self.assertRaises(ValueError):
            AvellanedaStoikovStrategy(AvellanedaStoikovConfig(min_spread=-0.001)) # min_spread < 0
        with self.assertRaises(ValueError):
            AvellanedaStoikovStrategy(AvellanedaStoikovConfig(order_size=0)) # order_size <= 0
        with self.assertRaises(ValueError):
            AvellanedaStoikovStrategy(AvellanedaStoikovConfig(max_inventory=-1)) # max_inventory < 0
        
        # Test current_time validation during order generation
        strategy = AvellanedaStoikovStrategy(self.default_config)
        market_data = {'mid_price': self.mid_price}
        with self.assertRaises(ValueError):
            strategy.generate_orders(market_data, 0, -1.0) # current_time < 0

    def test_min_spread_enforcement(self):
        """Test that min_spread is enforced when the theoretical spread is smaller."""
        # Configure parameters such that the optimal_half_spread would be very small
        config_tight_spread = AvellanedaStoikovConfig(
            gamma=100.0, # High gamma
            sigma=0.001,
            k=10000.0, # High k
            time_horizon_seconds=3600.0,
            min_spread=0.01 # Set a significant min_spread
        )
        strategy = AvellanedaStoikovStrategy(config_tight_spread)
        market_data = {'mid_price': self.mid_price}
        current_inventory = 0.0
        current_time = 0.0

        orders = strategy.generate_orders(market_data, current_inventory, current_time)

        # Theoretical half spread without min_spread:
        # (1/100) * log(1 + 100 / 10000) = 0.01 * log(1.01) approx 0.01 * 0.00995033 = 0.0000995033
        # This is much smaller than min_spread=0.01.
        # So, the effective half-spread should be min_spread.
        
        self.assertAlmostEqual(orders['ask_price'] - orders['bid_price'], 2 * config_tight_spread.min_spread, places=8)
        self.assertAlmostEqual(orders['ask_price'], self.mid_price + config_tight_spread.min_spread, places=8)
        self.assertAlmostEqual(orders['bid_price'], self.mid_price - config_tight_spread.min_spread, places=8)
    
    def test_max_inventory_exceeded(self):
        """Test behavior when max_inventory is exceeded, triggering liquidation orders."""
        config = self.default_config
        strategy = AvellanedaStoikovStrategy(config)
        market_data = {'mid_price': self.mid_price}

        # Test current_inventory > max_inventory (long excess)
        current_inventory = config.max_inventory + 5
        orders = strategy.generate_orders(market_data, current_inventory, 0.0)
        self.assertIsNone(orders['bid_price'])
        self.assertAlmostEqual(orders['ask_price'], self.mid_price + config.min_spread) # Liquidate at mid + min_spread
        self.assertAlmostEqual(orders['ask_amount'], 5.0) # Only liquidate the excess
        self.assertEqual(orders['bid_amount'], 0.0)

        # Test current_inventory < -max_inventory (short excess)
        current_inventory = -(config.max_inventory + 7)
        orders = strategy.generate_orders(market_data, current_inventory, 0.0)
        self.assertIsNone(orders['ask_price'])
        self.assertAlmostEqual(orders['bid_price'], self.mid_price - config.min_spread) # Liquidate at mid - min_spread
        self.assertAlmostEqual(orders['bid_amount'], 7.0) # Only liquidate the excess
        self.assertEqual(orders['ask_amount'], 0.0)

        # Test with current_inventory exactly at max_inventory (should behave normally)
        current_inventory = float(config.max_inventory)
        orders_at_max = strategy.generate_orders(market_data, current_inventory, 0.0)
        self.assertIsNotNone(orders_at_max['bid_price'])
        self.assertIsNotNone(orders_at_max['ask_price'])
        self.assertEqual(orders_at_max['bid_amount'], config.order_size)
        self.assertEqual(orders_at_max['ask_amount'], config.order_size)
        # Ensure it's following the strategy logic, not the liquidation logic
        expected_time_remaining = config.time_horizon_seconds - 0.0
        expected_inventory_risk_adjustment = current_inventory * config.gamma * config.sigma**2 * expected_time_remaining
        expected_reservation_price = self.mid_price - expected_inventory_risk_adjustment
        expected_half_spread = (1 / config.gamma) * math.log(1 + config.gamma / config.k)
        expected_half_spread = max(expected_half_spread, config.min_spread)
        self.assertAlmostEqual(orders_at_max['bid_price'], expected_reservation_price - expected_half_spread)
        self.assertAlmostEqual(orders_at_max['ask_price'], expected_reservation_price + expected_half_spread)


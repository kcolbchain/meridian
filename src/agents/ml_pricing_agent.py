"""
ML-Powered Pricing Agent for Meridian

A market-making agent that uses online machine learning to predict
short-term price movements and adjust bid/ask spreads dynamically.

Features:
- Exponential moving average (EMA) feature extraction
- Online linear regression for price direction prediction
- Volatility-adaptive spread with ML confidence weighting
- Inventory-aware skew using predicted direction
- Automatic feature scaling and model warm-up

This extends BaseAgent with a predict → quote → learn feedback loop:
  1. Extract features from market data (returns, volatility, momentum)
  2. Predict next-period return direction and confidence
  3. Set spread width based on volatility + model uncertainty
  4. Skew bid/ask based on inventory + predicted direction
  5. After fill, update model with realized outcome
"""

import math
import logging
from dataclasses import dataclass, field
from typing import Optional

from .base_agent import BaseAgent, Order, Side

logger = logging.getLogger(__name__)


@dataclass
class OnlineLinearModel:
    """
    Simple online linear regression using recursive least squares (RLS).
    Learns incrementally from each observation without storing history.
    """
    n_features: int
    weights: list = field(default_factory=list)
    _P: list = field(default_factory=list)  # Inverse covariance matrix (flattened)
    _n_samples: int = 0
    _forgetting_factor: float = 0.99  # Exponential forgetting for non-stationarity

    def __post_init__(self):
        self.weights = [0.0] * self.n_features
        # Initialize P as identity * large_value (high initial uncertainty)
        self._P = [0.0] * (self.n_features * self.n_features)
        for i in range(self.n_features):
            self._P[i * self.n_features + i] = 100.0

    def predict(self, features: list[float]) -> float:
        """Predict target value from features."""
        return sum(w * f for w, f in zip(self.weights, features))

    def update(self, features: list[float], target: float):
        """Update model with new observation using RLS."""
        n = self.n_features
        lam = self._forgetting_factor

        # Prediction error
        y_hat = self.predict(features)
        error = target - y_hat

        # P @ x
        Px = [0.0] * n
        for i in range(n):
            for j in range(n):
                Px[i] += self._P[i * n + j] * features[j]

        # x' @ P @ x + lambda
        denom = lam
        for j in range(n):
            denom += features[j] * Px[j]

        if abs(denom) < 1e-12:
            return  # Avoid numerical instability

        # Kalman gain
        gain = [px / denom for px in Px]

        # Update weights
        for i in range(n):
            self.weights[i] += gain[i] * error

        # Update P
        new_P = [0.0] * (n * n)
        for i in range(n):
            for j in range(n):
                new_P[i * n + j] = (
                    self._P[i * n + j] - gain[i] * Px[j]
                ) / lam
        self._P = new_P
        self._n_samples += 1

    @property
    def confidence(self) -> float:
        """Model confidence based on number of training samples."""
        if self._n_samples < 10:
            return 0.0
        return min(1.0, self._n_samples / 100.0)

    @property
    def weight_norm(self) -> float:
        return math.sqrt(sum(w * w for w in self.weights))


class MLPricingAgent(BaseAgent):
    """
    Market-making agent with online ML-powered spread and skew.

    Config keys:
        base_spread_bps: float    — Minimum spread in basis points (default: 10)
        volatility_mult: float    — Spread multiplier per unit volatility (default: 2.0)
        ml_skew_weight: float     — How much ML prediction influences skew [0,1] (default: 0.5)
        inventory_skew_bps: float — Max inventory-driven skew in bps (default: 5)
        ema_fast: int             — Fast EMA window (default: 5)
        ema_slow: int             — Slow EMA window (default: 20)
        warmup_ticks: int         — Ticks before ML predictions are used (default: 30)
        order_size: float         — Base order size (default: 1.0)
    """

    N_FEATURES = 5  # [return_1, return_5, volatility, momentum, inventory_ratio]

    def __init__(self, agent_id: str, config: dict):
        super().__init__(agent_id, config)

        self.model = OnlineLinearModel(n_features=self.N_FEATURES)
        self._price_history: list[float] = []
        self._tick_count: int = 0
        self._last_prediction: float = 0.0
        self._last_features: Optional[list[float]] = None

    def _ema(self, prices: list[float], window: int) -> float:
        """Compute exponential moving average."""
        if not prices:
            return 0.0
        alpha = 2.0 / (window + 1)
        ema = prices[0]
        for p in prices[1:]:
            ema = alpha * p + (1 - alpha) * ema
        return ema

    def _extract_features(self, market_data: dict) -> list[float]:
        """Extract ML features from market data and price history."""
        prices = self._price_history
        mid_price = market_data.get("mid_price", prices[-1] if prices else 0)

        # Return over last 1 tick
        ret_1 = (prices[-1] / prices[-2] - 1.0) if len(prices) >= 2 else 0.0

        # Return over last 5 ticks
        ret_5 = (prices[-1] / prices[-6] - 1.0) if len(prices) >= 6 else 0.0

        # Realized volatility (std of returns over last 20 ticks)
        if len(prices) >= 3:
            returns = [(prices[i] / prices[i-1] - 1.0) for i in range(max(1, len(prices)-20), len(prices))]
            mean_ret = sum(returns) / len(returns)
            vol = math.sqrt(sum((r - mean_ret) ** 2 for r in returns) / len(returns))
        else:
            vol = 0.001  # Default volatility

        # Momentum: fast EMA / slow EMA - 1
        ema_fast = self.config.get("ema_fast", 5)
        ema_slow = self.config.get("ema_slow", 20)
        fast = self._ema(prices[-ema_fast:], ema_fast) if len(prices) >= ema_fast else mid_price
        slow = self._ema(prices[-ema_slow:], ema_slow) if len(prices) >= ema_slow else mid_price
        momentum = (fast / slow - 1.0) if slow > 0 else 0.0

        # Inventory ratio (normalized position)
        max_exposure = self.config.get("max_exposure", 10.0)
        inv_ratio = self.position.net_exposure / max_exposure if max_exposure > 0 else 0.0

        return [ret_1, ret_5, vol, momentum, inv_ratio]

    def evaluate_market(self, market_data: dict) -> dict:
        """Evaluate market and generate ML-powered signals."""
        mid_price = market_data.get("mid_price", 0)
        self._price_history.append(mid_price)
        self._tick_count += 1

        # Keep bounded history
        if len(self._price_history) > 500:
            self._price_history = self._price_history[-500:]

        # Learn from previous prediction
        if self._last_features is not None and len(self._price_history) >= 2:
            actual_return = self._price_history[-1] / self._price_history[-2] - 1.0
            self.model.update(self._last_features, actual_return)

        # Extract features and predict
        features = self._extract_features(market_data)
        self._last_features = features

        warmup = self.config.get("warmup_ticks", 30)
        if self._tick_count < warmup:
            predicted_return = 0.0
            ml_confidence = 0.0
        else:
            predicted_return = self.model.predict(features)
            ml_confidence = self.model.confidence

        self._last_prediction = predicted_return

        # Volatility from features
        volatility = features[2]

        self.log_event("ml_signal", {
            "predicted_return": round(predicted_return, 6),
            "ml_confidence": round(ml_confidence, 3),
            "volatility": round(volatility, 6),
            "inventory_ratio": round(features[4], 3),
            "weight_norm": round(self.model.weight_norm, 4),
        })

        return {
            "mid_price": mid_price,
            "predicted_return": predicted_return,
            "ml_confidence": ml_confidence,
            "volatility": volatility,
            "inventory_ratio": features[4],
        }

    def execute_strategy(self, signals: dict) -> list[Order]:
        """Generate bid/ask orders with ML-adjusted spread and skew."""
        mid_price = signals["mid_price"]
        if mid_price <= 0:
            return []

        predicted_return = signals["predicted_return"]
        ml_confidence = signals["ml_confidence"]
        volatility = signals["volatility"]
        inv_ratio = signals["inventory_ratio"]

        base_spread_bps = self.config.get("base_spread_bps", 10.0)
        vol_mult = self.config.get("volatility_mult", 2.0)
        ml_skew_weight = self.config.get("ml_skew_weight", 0.5)
        inv_skew_bps = self.config.get("inventory_skew_bps", 5.0)
        order_size = self.config.get("order_size", 1.0)

        # Spread: base + volatility-driven widening
        spread_bps = base_spread_bps + vol_mult * volatility * 10000
        half_spread = (spread_bps / 10000) / 2 * mid_price

        # ML-driven skew: predicted direction shifts the mid
        ml_skew = predicted_return * ml_confidence * ml_skew_weight * mid_price

        # Inventory skew: discourage increasing exposure
        # Long inventory → lower bid, higher ask (encourage selling)
        inventory_skew = inv_ratio * (inv_skew_bps / 10000) * mid_price

        bid_price = mid_price - half_spread - inventory_skew + ml_skew
        ask_price = mid_price + half_spread - inventory_skew + ml_skew

        # Ensure bid < ask
        if bid_price >= ask_price:
            mid = (bid_price + ask_price) / 2
            bid_price = mid - 0.01
            ask_price = mid + 0.01

        orders = [
            Order(side=Side.BID, price=round(bid_price, 4), size=order_size),
            Order(side=Side.ASK, price=round(ask_price, 4), size=order_size),
        ]

        self.log_event("quote", {
            "bid": bid_price,
            "ask": ask_price,
            "spread_bps": round(spread_bps, 2),
            "ml_skew": round(ml_skew, 4),
            "inv_skew": round(inventory_skew, 4),
        })

        return orders

    def rebalance(self) -> list[Order]:
        """Emergency rebalance when inventory exceeds limits."""
        exposure = self.position.net_exposure
        order_size = self.config.get("order_size", 1.0)

        if exposure > 0:
            # Too long — add aggressive sell
            return [Order(
                side=Side.ASK,
                price=self._price_history[-1] * 0.999,  # Aggressive ask
                size=min(abs(exposure) * 0.5, order_size * 2),
            )]
        elif exposure < 0:
            # Too short — add aggressive buy
            return [Order(
                side=Side.BID,
                price=self._price_history[-1] * 1.001,  # Aggressive bid
                size=min(abs(exposure) * 0.5, order_size * 2),
            )]
        return []

    def get_model_diagnostics(self) -> dict:
        """Return ML model state for monitoring."""
        return {
            "n_samples": self.model._n_samples,
            "confidence": self.model.confidence,
            "weights": list(self.model.weights),
            "weight_norm": self.model.weight_norm,
            "last_prediction": self._last_prediction,
            "tick_count": self._tick_count,
        }

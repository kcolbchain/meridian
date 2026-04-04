"""
Risk Management System for Market Making Agent
Handles position sizing, stop losses, and risk controls
"""
import asyncio
import time
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum
import logging
import numpy as np
from collections import deque

from config import config
from wallet_manager import WalletInfo
from market_analyzer import MarketMetrics
from trading_engine import TradingEngine, OrderSide

logger = logging.getLogger(__name__)

class RiskLevel(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

class RiskEvent(Enum):
    DAILY_LOSS_LIMIT = "daily_loss_limit"
    POSITION_SIZE_LIMIT = "position_size_limit"
    VOLATILITY_SPIKE = "volatility_spike"
    LIQUIDITY_CRISIS = "liquidity_crisis"
    CONSECUTIVE_LOSSES = "consecutive_losses"
    MARKET_CRASH = "market_crash"

@dataclass
class RiskMetrics:
    """Risk assessment metrics"""
    current_risk_level: RiskLevel
    daily_pnl: float
    daily_pnl_percentage: float
    max_drawdown: float
    position_size_ratio: float
    volatility_risk: float
    liquidity_risk: float
    consecutive_losses: int
    last_updated: float = field(default_factory=time.time)

@dataclass
class RiskAlert:
    """Risk alert"""
    event_type: RiskEvent
    severity: RiskLevel
    message: str
    timestamp: float
    wallet_address: Optional[str] = None
    recommended_action: Optional[str] = None

@dataclass
class PositionSizing:
    """Position sizing parameters"""
    max_position_size: float
    current_position_size: float
    recommended_size: float
    risk_adjusted_size: float
    kelly_fraction: float

class RiskManager:
    """Manages risk across all trading operations"""
    
    def __init__(self, trading_engine: TradingEngine):
        self.trading_engine = trading_engine
        self.risk_metrics: Dict[str, RiskMetrics] = {}
        self.risk_alerts: List[RiskAlert] = []
        self.daily_pnl_history: deque = deque(maxlen=30)  # 30 days
        self.trade_history: deque = deque(maxlen=1000)  # Last 1000 trades
        
        # Risk parameters
        self.max_daily_loss = config.trading.max_daily_loss
        self.stop_loss_percentage = config.trading.stop_loss_percentage
        self.take_profit_percentage = config.trading.take_profit_percentage
        self.max_position_size = config.trading.max_position_size
        
        # Risk thresholds
        self.volatility_threshold_high = 15.0  # 15%
        self.volatility_threshold_critical = 25.0  # 25%
        self.liquidity_threshold_low = 0.3  # 30%
        self.consecutive_loss_limit = 5
        
        # Kelly Criterion parameters
        self.kelly_lookback_days = 7
        self.kelly_max_fraction = 0.25  # Maximum 25% of capital per trade
    
    async def assess_risk(self, wallet_info: WalletInfo, market_metrics: MarketMetrics) -> RiskMetrics:
        """Assess risk for a specific wallet"""
        try:
            wallet_address = wallet_info.address
            
            # Calculate daily PnL
            daily_pnl = self.trading_engine.daily_pnl
            daily_pnl_percentage = (daily_pnl / max(wallet_info.balance_sol, 0.01)) * 100
            
            # Calculate position size ratio
            total_balance = wallet_info.balance_sol + wallet_info.balance_base_token
            position_size_ratio = wallet_info.balance_base_token / max(total_balance, 0.01)
            
            # Calculate volatility risk
            volatility_risk = self._calculate_volatility_risk(market_metrics)
            
            # Calculate liquidity risk
            liquidity_risk = self._calculate_liquidity_risk(market_metrics)
            
            # Count consecutive losses
            consecutive_losses = self._count_consecutive_losses(wallet_address)
            
            # Calculate max drawdown
            max_drawdown = self._calculate_max_drawdown(wallet_address)
            
            # Determine overall risk level
            risk_level = self._determine_risk_level(
                daily_pnl_percentage,
                position_size_ratio,
                volatility_risk,
                liquidity_risk,
                consecutive_losses
            )
            
            # Create risk metrics
            risk_metrics = RiskMetrics(
                current_risk_level=risk_level,
                daily_pnl=daily_pnl,
                daily_pnl_percentage=daily_pnl_percentage,
                max_drawdown=max_drawdown,
                position_size_ratio=position_size_ratio,
                volatility_risk=volatility_risk,
                liquidity_risk=liquidity_risk,
                consecutive_losses=consecutive_losses
            )
            
            self.risk_metrics[wallet_address] = risk_metrics
            
            # Check for risk alerts
            await self._check_risk_alerts(wallet_address, risk_metrics)
            
            return risk_metrics
            
        except Exception as e:
            logger.error(f"Failed to assess risk for wallet {wallet_info.address}: {e}")
            return RiskMetrics(
                current_risk_level=RiskLevel.HIGH,
                daily_pnl=0,
                daily_pnl_percentage=0,
                max_drawdown=0,
                position_size_ratio=0,
                volatility_risk=0,
                liquidity_risk=0,
                consecutive_losses=0
            )
    
    def _calculate_volatility_risk(self, market_metrics: MarketMetrics) -> float:
        """Calculate volatility-based risk score"""
        volatility = market_metrics.volatility
        
        if volatility < 5.0:
            return 0.1  # Low risk
        elif volatility < 10.0:
            return 0.3  # Medium risk
        elif volatility < 15.0:
            return 0.6  # High risk
        else:
            return 1.0  # Critical risk
    
    def _calculate_liquidity_risk(self, market_metrics: MarketMetrics) -> float:
        """Calculate liquidity-based risk score"""
        liquidity_score = market_metrics.liquidity_score
        
        if liquidity_score > 0.7:
            return 0.1  # Low risk
        elif liquidity_score > 0.5:
            return 0.3  # Medium risk
        elif liquidity_score > 0.3:
            return 0.6  # High risk
        else:
            return 1.0  # Critical risk
    
    def _count_consecutive_losses(self, wallet_address: str) -> int:
        """Count consecutive losing trades for a wallet"""
        consecutive_losses = 0
        
        # Get recent trades for this wallet
        wallet_trades = [trade for trade in self.trade_history 
                        if trade.wallet_address == wallet_address]
        
        # Count consecutive losses from most recent
        for trade in reversed(wallet_trades[-10:]):  # Check last 10 trades
            if trade.side == OrderSide.SELL:  # Assuming sell trades are profit-taking
                break
            consecutive_losses += 1
        
        return consecutive_losses
    
    def _calculate_max_drawdown(self, wallet_address: str) -> float:
        """Calculate maximum drawdown for a wallet"""
        # This is a simplified calculation
        # In production, you'd track peak equity and current equity
        wallet_trades = [trade for trade in self.trade_history 
                        if trade.wallet_address == wallet_address]
        
        if not wallet_trades:
            return 0.0
        
        # Calculate cumulative PnL
        cumulative_pnl = 0
        peak_pnl = 0
        max_drawdown = 0
        
        for trade in wallet_trades:
            # Simplified PnL calculation
            if trade.side == OrderSide.SELL:
                cumulative_pnl += trade.amount * trade.price * 0.001  # Simplified profit
            else:
                cumulative_pnl -= trade.amount * trade.price * 0.001  # Simplified loss
            
            if cumulative_pnl > peak_pnl:
                peak_pnl = cumulative_pnl
            
            drawdown = peak_pnl - cumulative_pnl
            if drawdown > max_drawdown:
                max_drawdown = drawdown
        
        return max_drawdown
    
    def _determine_risk_level(self, daily_pnl_percentage: float, position_size_ratio: float,
                            volatility_risk: float, liquidity_risk: float, 
                            consecutive_losses: int) -> RiskLevel:
        """Determine overall risk level"""
        risk_score = 0
        
        # Daily PnL risk
        if daily_pnl_percentage < -self.max_daily_loss * 100:
            risk_score += 3
        elif daily_pnl_percentage < -self.max_daily_loss * 50:
            risk_score += 2
        elif daily_pnl_percentage < 0:
            risk_score += 1
        
        # Position size risk
        if position_size_ratio > 0.8:
            risk_score += 2
        elif position_size_ratio > 0.6:
            risk_score += 1
        
        # Volatility risk
        risk_score += volatility_risk * 2
        
        # Liquidity risk
        risk_score += liquidity_risk * 2
        
        # Consecutive losses risk
        if consecutive_losses >= self.consecutive_loss_limit:
            risk_score += 2
        elif consecutive_losses >= 3:
            risk_score += 1
        
        # Determine risk level
        if risk_score >= 6:
            return RiskLevel.CRITICAL
        elif risk_score >= 4:
            return RiskLevel.HIGH
        elif risk_score >= 2:
            return RiskLevel.MEDIUM
        else:
            return RiskLevel.LOW
    
    async def _check_risk_alerts(self, wallet_address: str, risk_metrics: RiskMetrics):
        """Check for risk alerts and create them if needed"""
        alerts = []
        
        # Daily loss limit alert
        if risk_metrics.daily_pnl_percentage < -self.max_daily_loss * 100:
            alerts.append(RiskAlert(
                event_type=RiskEvent.DAILY_LOSS_LIMIT,
                severity=RiskLevel.CRITICAL,
                message=f"Daily loss limit exceeded: {risk_metrics.daily_pnl_percentage:.2f}%",
                timestamp=time.time(),
                wallet_address=wallet_address,
                recommended_action="Stop all trading and review strategy"
            ))
        
        # Position size limit alert
        if risk_metrics.position_size_ratio > 0.8:
            alerts.append(RiskAlert(
                event_type=RiskEvent.POSITION_SIZE_LIMIT,
                severity=RiskLevel.HIGH,
                message=f"Position size too large: {risk_metrics.position_size_ratio:.2f}",
                timestamp=time.time(),
                wallet_address=wallet_address,
                recommended_action="Reduce position size"
            ))
        
        # Volatility spike alert
        if risk_metrics.volatility_risk > 0.8:
            alerts.append(RiskAlert(
                event_type=RiskEvent.VOLATILITY_SPIKE,
                severity=RiskLevel.HIGH,
                message=f"High volatility detected: {risk_metrics.volatility_risk:.2f}",
                timestamp=time.time(),
                wallet_address=wallet_address,
                recommended_action="Reduce position sizes and increase spreads"
            ))
        
        # Liquidity crisis alert
        if risk_metrics.liquidity_risk > 0.8:
            alerts.append(RiskAlert(
                event_type=RiskEvent.LIQUIDITY_CRISIS,
                severity=RiskLevel.HIGH,
                message=f"Low liquidity detected: {risk_metrics.liquidity_risk:.2f}",
                timestamp=time.time(),
                wallet_address=wallet_address,
                recommended_action="Increase spreads and reduce order sizes"
            ))
        
        # Consecutive losses alert
        if risk_metrics.consecutive_losses >= self.consecutive_loss_limit:
            alerts.append(RiskAlert(
                event_type=RiskEvent.CONSECUTIVE_LOSSES,
                severity=RiskLevel.MEDIUM,
                message=f"Consecutive losses: {risk_metrics.consecutive_losses}",
                timestamp=time.time(),
                wallet_address=wallet_address,
                recommended_action="Review trading strategy and reduce position sizes"
            ))
        
        # Add alerts to history
        self.risk_alerts.extend(alerts)
        
        # Log critical alerts
        for alert in alerts:
            if alert.severity in [RiskLevel.CRITICAL, RiskLevel.HIGH]:
                logger.warning(f"Risk Alert [{alert.severity.value}]: {alert.message}")
    
    def calculate_position_size(self, wallet_info: WalletInfo, market_metrics: MarketMetrics) -> PositionSizing:
        """Calculate optimal position size using Kelly Criterion and risk management"""
        try:
            # Base position size
            base_balance = wallet_info.balance_sol
            max_position_size = min(
                self.max_position_size,
                base_balance * 0.1  # Maximum 10% of balance per trade
            )
            
            # Current position size
            current_position_size = wallet_info.balance_base_token
            
            # Calculate Kelly fraction
            kelly_fraction = self._calculate_kelly_fraction(wallet_info.address)
            
            # Risk-adjusted position size
            risk_metrics = self.risk_metrics.get(wallet_info.address)
            if risk_metrics:
                risk_multiplier = self._get_risk_multiplier(risk_metrics.current_risk_level)
            else:
                risk_multiplier = 1.0
            
            # Recommended size
            recommended_size = max_position_size * kelly_fraction * risk_multiplier
            
            # Ensure minimum size
            recommended_size = max(recommended_size, config.trading.min_order_size)
            
            return PositionSizing(
                max_position_size=max_position_size,
                current_position_size=current_position_size,
                recommended_size=recommended_size,
                risk_adjusted_size=recommended_size * risk_multiplier,
                kelly_fraction=kelly_fraction
            )
            
        except Exception as e:
            logger.error(f"Failed to calculate position size: {e}")
            return PositionSizing(
                max_position_size=config.trading.min_order_size,
                current_position_size=0,
                recommended_size=config.trading.min_order_size,
                risk_adjusted_size=config.trading.min_order_size,
                kelly_fraction=0.1
            )
    
    def _calculate_kelly_fraction(self, wallet_address: str) -> float:
        """Calculate Kelly Criterion fraction for position sizing"""
        try:
            # Get recent trades for this wallet
            wallet_trades = [trade for trade in self.trade_history 
                           if trade.wallet_address == wallet_address]
            
            if len(wallet_trades) < 10:  # Need sufficient history
                return 0.1  # Conservative default
            
            # Calculate win rate and average win/loss
            wins = []
            losses = []
            
            for trade in wallet_trades[-50:]:  # Use last 50 trades
                # Simplified PnL calculation
                if trade.side == OrderSide.SELL:
                    pnl = trade.amount * trade.price * 0.001  # Simplified profit
                    if pnl > 0:
                        wins.append(pnl)
                    else:
                        losses.append(abs(pnl))
                else:
                    pnl = -trade.amount * trade.price * 0.001  # Simplified loss
                    if pnl > 0:
                        wins.append(pnl)
                    else:
                        losses.append(abs(pnl))
            
            if not wins or not losses:
                return 0.1  # Conservative default
            
            # Calculate Kelly fraction
            win_rate = len(wins) / (len(wins) + len(losses))
            avg_win = np.mean(wins)
            avg_loss = np.mean(losses)
            
            if avg_loss == 0:
                return 0.1
            
            kelly_fraction = (win_rate * avg_win - (1 - win_rate) * avg_loss) / avg_win
            
            # Cap the Kelly fraction
            kelly_fraction = max(0, min(kelly_fraction, self.kelly_max_fraction))
            
            return kelly_fraction
            
        except Exception as e:
            logger.error(f"Failed to calculate Kelly fraction: {e}")
            return 0.1
    
    def _get_risk_multiplier(self, risk_level: RiskLevel) -> float:
        """Get position size multiplier based on risk level"""
        multipliers = {
            RiskLevel.LOW: 1.0,
            RiskLevel.MEDIUM: 0.7,
            RiskLevel.HIGH: 0.4,
            RiskLevel.CRITICAL: 0.1
        }
        return multipliers.get(risk_level, 0.1)
    
    def should_trade(self, wallet_address: str) -> Tuple[bool, str]:
        """Determine if trading should be allowed for a wallet"""
        risk_metrics = self.risk_metrics.get(wallet_address)
        
        if not risk_metrics:
            return True, "No risk data available"
        
        # Check critical risk conditions
        if risk_metrics.current_risk_level == RiskLevel.CRITICAL:
            return False, "Critical risk level - trading suspended"
        
        if risk_metrics.daily_pnl_percentage < -self.max_daily_loss * 100:
            return False, "Daily loss limit exceeded"
        
        if risk_metrics.consecutive_losses >= self.consecutive_loss_limit:
            return False, "Too many consecutive losses"
        
        # Check high risk conditions
        if risk_metrics.current_risk_level == RiskLevel.HIGH:
            return False, "High risk level - trading suspended"
        
        return True, "Trading allowed"
    
    def get_risk_summary(self) -> Dict:
        """Get overall risk summary"""
        if not self.risk_metrics:
            return {}
        
        total_wallets = len(self.risk_metrics)
        critical_wallets = sum(1 for rm in self.risk_metrics.values() 
                             if rm.current_risk_level == RiskLevel.CRITICAL)
        high_risk_wallets = sum(1 for rm in self.risk_metrics.values() 
                              if rm.current_risk_level == RiskLevel.HIGH)
        
        avg_daily_pnl = np.mean([rm.daily_pnl_percentage for rm in self.risk_metrics.values()])
        max_drawdown = max([rm.max_drawdown for rm in self.risk_metrics.values()])
        
        return {
            "total_wallets": total_wallets,
            "critical_risk_wallets": critical_wallets,
            "high_risk_wallets": high_risk_wallets,
            "average_daily_pnl_percentage": avg_daily_pnl,
            "max_drawdown": max_drawdown,
            "total_alerts": len(self.risk_alerts),
            "recent_alerts": len([a for a in self.risk_alerts if time.time() - a.timestamp < 3600])
        }
    
    def add_trade(self, trade):
        """Add a trade to history for risk analysis"""
        self.trade_history.append(trade)
    
    def get_recent_alerts(self, hours: int = 24) -> List[RiskAlert]:
        """Get recent risk alerts"""
        cutoff_time = time.time() - (hours * 3600)
        return [alert for alert in self.risk_alerts if alert.timestamp >= cutoff_time]
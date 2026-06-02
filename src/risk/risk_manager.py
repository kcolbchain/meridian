"""
Risk management stub for market-making agents.

Provides a placeholder interface that downstream agent code can depend on.
Currently returns default (lenient) risk assessments — no real risk logic
is wired in until a dedicated risk module is designed against the live
strategies/ and oracle/ surfaces.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class RiskLevel(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass
class RiskAssessment:
    """Result of evaluating a quote proposal against current market context.
    
    Note: all fields are currently hard-coded defaults.  Real risk logic
    (position sizing against wallet balance, volatility gating from
    oracle feeds, consecutive-loss tracking, …) should be added here.
    """

    risk_level: RiskLevel = RiskLevel.LOW
    score: float = 0.0  # 0 = safe, 1 = dangerous
    reasons: list[str] = field(default_factory=list)
    evaluated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def should_skip(self) -> bool:
        """Return True when the quote should *not* be submitted."""
        return self.score >= 1.0


def evaluate(
    bid_price: float,
    ask_price: float,
    spread_bps: float | None = None,
    mid_price: float | None = None,
) -> RiskAssessment:
    """Evaluate a proposed bid/ask quote and return a risk assessment.
    
    Current implementation is a no-op stub that always approves the
    quote.  Real implementations should inspect market conditions from
    oracle feeds (see ``src.oracle.price_feed``), check wallet limits,
    and return a meaningful ``RiskAssessment``.
    """
    return RiskAssessment()

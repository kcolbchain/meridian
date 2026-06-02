"""Tests for the risk management stub."""

from datetime import datetime, timezone
import pytest

from src.risk import evaluate, RiskAssessment, RiskLevel


class TestRiskAssessmentDataclass:
    def test_defaults_are_lenient(self):
        r = RiskAssessment()
        assert r.risk_level == RiskLevel.LOW
        assert r.score == 0.0
        assert r.reasons == []
        assert r.should_skip is False

    def test_skip_when_score_at_threshold(self):
        r = RiskAssessment(score=1.0)
        assert r.should_skip is True

    def test_skip_when_score_exceeds_threshold(self):
        r = RiskAssessment(score=1.5)
        assert r.should_skip is True

    def test_evaluated_at_set_on_creation(self):
        r = RiskAssessment()
        assert isinstance(r.evaluated_at, datetime)
        assert r.evaluated_at.tzinfo is not None


class TestEvaluateStub:
    def test_returns_risk_assessment(self):
        result = evaluate(bid_price=99.0, ask_price=101.0)
        assert isinstance(result, RiskAssessment)

    def test_accepts_spread_bps_and_mid_price(self):
        result = evaluate(
            bid_price=99.50,
            ask_price=100.50,
            spread_bps=100,
            mid_price=100.0,
        )
        assert isinstance(result, RiskAssessment)

    def test_always_lenient(self):
        for _ in range(10):
            r = evaluate(bid_price=95.0, ask_price=105.0)
            assert r.risk_level == RiskLevel.LOW
            assert r.should_skip is False

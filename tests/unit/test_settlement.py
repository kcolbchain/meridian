"""Tests for settlement module."""

from src.settlement import CR8USDSettlement, SettlementConfig, SettlementType


class TestSettlementConfig:
    def test_default_usdc(self):
        cfg = SettlementConfig()
        assert cfg.settlement_asset == SettlementType.USDC

    def test_cr8_usd_config(self):
        cfg = SettlementConfig(settlement_asset=SettlementType.CR8_USD, burn_toll_bps=5.0)
        ss = CR8USDSettlement(cfg)
        assert ss.is_cr8_usd
        assert ss.settlement_symbol == "CR8-USD"

    def test_custom_asset(self):
        cfg = SettlementConfig(settlement_asset=SettlementType.CUSTOM, custom_asset_symbol="FOO")
        ss = CR8USDSettlement(cfg)
        assert ss.settlement_symbol == "FOO"
        assert not ss.is_cr8_usd


class TestSettlementFill:
    def test_usdc_fill_no_burn(self):
        ss = CR8USDSettlement(SettlementConfig())
        result = ss.settle_fill("tx1", "bid", 100.0, 1.0, 0.5)
        assert result["burn_toll"] == 0.0
        assert result["settlement_asset"] == "USDC"

    def test_cr8_usd_fill_with_burn(self):
        cfg = SettlementConfig(
            settlement_asset=SettlementType.CR8_USD,
            burn_toll_bps=10.0,
        )
        ss = CR8USDSettlement(cfg)
        result = ss.settle_fill("tx2", "ask", 200.0, 1.5, 1.0)
        assert result["burn_toll"] > 0
        assert result["settlement_asset"] == "CR8-USD"

    def test_burn_event_recorded(self):
        cfg = SettlementConfig(
            settlement_asset=SettlementType.CR8_USD,
            burn_toll_bps=50.0,
        )
        ss = CR8USDSettlement(cfg)
        ss.settle_fill("tx3", "bid", 100.0, 10.0, 0.1)
        events = ss.get_burn_events()
        assert len(events) == 1
        assert events[0].tx_id == "tx3"
        assert events[0].burn_toll > 0


class TestPnLTracking:
    def test_per_asset_pnl_accumulates(self):
        ss = CR8USDSettlement(SettlementConfig(
            settlement_asset=SettlementType.CR8_USD,
            burn_toll_bps=10.0,
        ))
        ss.settle_fill("t1", "bid", 100.0, 1.0, 0.5)
        ss.settle_fill("t2", "ask", 101.0, 0.5, 0.3)
        pnls = ss.get_pnl()
        assert len(pnls) == 1
        assert pnls[0].asset == "CR8-USD"
        assert pnls[0].total_volume > 0
        assert pnls[0].burn_toll_paid > 0

    def test_pnl_filter_by_asset(self):
        ss = CR8USDSettlement(SettlementConfig(settlement_asset=SettlementType.USDC))
        ss.settle_fill("t1", "bid", 50.0, 2.0, 0.1)
        pnls = ss.get_pnl("USDC")
        assert len(pnls) == 1
        pnls = ss.get_pnl("CR8-USD")
        assert len(pnls) == 0

"""Tests for multi-chain EVM connector (issue #3)."""

import pytest
from src.chains.evm.connector import (
    EVMConnector,
    ChainConfig,
    MultiChainManager,
    CHAIN_CONFIGS,
    SUPPORTED_CHAINS,
)


class TestChainConfigs:
    def test_supported_chains(self):
        assert "ethereum" in SUPPORTED_CHAINS
        assert "arbitrum" in SUPPORTED_CHAINS
        assert "optimism" in SUPPORTED_CHAINS
        assert "base" in SUPPORTED_CHAINS

    def test_chain_has_required_fields(self):
        for chain, config in CHAIN_CONFIGS.items():
            assert "chain_id" in config
            assert "uniswap_router" in config
            assert "weth" in config
            assert "usdc" in config
            assert "block_time_ms" in config

    def test_l2_chains_have_lower_gas_multiplier(self):
        eth_mult = CHAIN_CONFIGS["ethereum"]["gas_multiplier"]
        for chain in ["arbitrum", "optimism", "base"]:
            assert CHAIN_CONFIGS[chain]["gas_multiplier"] < eth_mult


class TestEVMConnector:
    def test_create_ethereum_connector(self):
        conn = EVMConnector("ethereum", simulate=True)
        assert conn.chain == "ethereum"
        assert conn.chain_id == 1

    def test_create_arbitrum_connector(self):
        conn = EVMConnector("arbitrum", simulate=True)
        assert conn.chain_id == 42161

    def test_create_optimism_connector(self):
        conn = EVMConnector("optimism", simulate=True)
        assert conn.chain_id == 10

    def test_create_base_connector(self):
        conn = EVMConnector("base", simulate=True)
        assert conn.chain_id == 8453

    def test_unsupported_chain_raises(self):
        with pytest.raises(ValueError, match="Unsupported chain"):
            EVMConnector("solana")

    def test_simulate_transaction(self):
        conn = EVMConnector("ethereum", simulate=True)
        result = conn.simulate_transaction("0xfrom", "0xto", 1.0)
        assert result["simulated"] is True
        assert "tx_hash" in result
        assert result["chain"] == "ethereum"

    def test_gas_estimation_chain_specific(self):
        eth = EVMConnector("ethereum", simulate=True)
        arb = EVMConnector("arbitrum", simulate=True)
        base_gas = 200000
        assert eth.estimate_gas(base_gas) > arb.estimate_gas(base_gas)

    def test_block_time(self):
        eth = EVMConnector("ethereum", simulate=True)
        assert eth.get_block_time_seconds() == 12.0
        arb = EVMConnector("arbitrum", simulate=True)
        assert arb.get_block_time_seconds() == 0.25

    def test_is_connected_simulate(self):
        conn = EVMConnector("ethereum", simulate=True)
        assert conn.is_connected


class TestMultiChainManager:
    def test_add_chain(self):
        mgr = MultiChainManager(simulate=True)
        conn = mgr.add_chain("ethereum")
        assert conn is not None
        assert "ethereum" in mgr.connectors

    def test_get_connector(self):
        mgr = MultiChainManager(simulate=True)
        mgr.add_chain("arbitrum")
        conn = mgr.get_connector("arbitrum")
        assert conn is not None
        assert conn.chain == "arbitrum"

    def test_get_nonexistent_connector(self):
        mgr = MultiChainManager(simulate=True)
        assert mgr.get_connector("polygon") is None

    def test_get_supported_chains(self):
        mgr = MultiChainManager()
        chains = mgr.get_supported_chains()
        assert len(chains) == 4

    def test_get_connected_chains(self):
        mgr = MultiChainManager(simulate=True)
        mgr.add_chain("ethereum")
        mgr.add_chain("arbitrum")
        connected = mgr.get_connected_chains()
        assert len(connected) == 2

    def test_multiple_chains_independent(self):
        mgr = MultiChainManager(simulate=True)
        eth = mgr.add_chain("ethereum")
        arb = mgr.add_chain("arbitrum")
        assert eth.chain_id != arb.chain_id
        assert eth.estimate_gas(100000) > arb.estimate_gas(100000)

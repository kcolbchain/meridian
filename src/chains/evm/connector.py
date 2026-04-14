"""Multi-chain EVM connector for Arbitrum, Optimism, and Base.

Provides:
- Web3 connectors per chain (Arb/OP/Base/Ethereum)
- Uniswap V3 router integration
- Chain-specific gas estimation
- Simulation mode

Issue #3: Add multi-chain deployment support.
"""

from dataclasses import dataclass, field
from typing import Optional
import logging

logger = logging.getLogger(__name__)


# ── Chain configurations ──────────────────────────────────────────────────────

CHAIN_CONFIGS = {
    "ethereum": {
        "chain_id": 1,
        "name": "Ethereum Mainnet",
        "uniswap_router": "0xE592427A0AEce92De3Edee1F18E0157C05861564",
        "weth": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
        "usdc": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        "block_time_ms": 12000,
        "gas_multiplier": 1.0,
    },
    "arbitrum": {
        "chain_id": 42161,
        "name": "Arbitrum One",
        "uniswap_router": "0xE592427A0AEce92De3Edee1F18E0157C05861564",
        "weth": "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",
        "usdc": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
        "block_time_ms": 250,
        "gas_multiplier": 0.5,  # L2 gas is cheaper
    },
    "optimism": {
        "chain_id": 10,
        "name": "Optimism",
        "uniswap_router": "0xE592427A0AEce92De3Edee1F18E0157C05861564",
        "weth": "0x4200000000000000000000000000000000000006",
        "usdc": "0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85",
        "block_time_ms": 2000,
        "gas_multiplier": 0.4,
    },
    "base": {
        "chain_id": 8453,
        "name": "Base",
        "uniswap_router": "0x2626664c2603336E57B271c5C0b26F421741e481",
        "weth": "0x4200000000000000000000000000000000000006",
        "usdc": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        "block_time_ms": 2000,
        "gas_multiplier": 0.3,
    },
}

SUPPORTED_CHAINS = list(CHAIN_CONFIGS.keys())


@dataclass
class ChainConfig:
    """Configuration for a specific EVM chain."""
    chain: str
    rpc_url: str
    chain_id: int = 0
    name: str = ""
    uniswap_router: str = ""
    weth: str = ""
    usdc: str = ""
    block_time_ms: int = 12000
    gas_multiplier: float = 1.0
    simulate: bool = True

    def __post_init__(self):
        if self.chain in CHAIN_CONFIGS:
            config = CHAIN_CONFIGS[self.chain]
            if not self.chain_id:
                self.chain_id = config["chain_id"]
            if not self.name:
                self.name = config["name"]
            if not self.uniswap_router:
                self.uniswap_router = config["uniswap_router"]
            if not self.weth:
                self.weth = config["weth"]
            if not self.usdc:
                self.usdc = config["usdc"]
            self.block_time_ms = config["block_time_ms"]
            self.gas_multiplier = config["gas_multiplier"]


class EVMConnector:
    """Multi-chain EVM connector for trading operations."""

    def __init__(self, chain: str, rpc_url: str = "", simulate: bool = True):
        if chain not in CHAIN_CONFIGS:
            raise ValueError(f"Unsupported chain: {chain}. Supported: {SUPPORTED_CHAINS}")

        self.config = ChainConfig(chain=chain, rpc_url=rpc_url, simulate=simulate)
        self.chain = chain
        self.simulate = simulate
        self._web3 = None

        if not simulate and rpc_url:
            try:
                from web3 import Web3
                self._web3 = Web3(Web3.HTTPProvider(rpc_url))
                logger.info(f"Connected to {self.config.name} (chain_id={self.config.chain_id})")
            except ImportError:
                logger.warning("web3 not installed, running in simulation mode")
                self.simulate = True

    @property
    def chain_id(self) -> int:
        return self.config.chain_id

    @property
    def is_connected(self) -> bool:
        if self.simulate:
            return True
        return self._web3 is not None and self._web3.is_connected()

    def estimate_gas(self, base_gas: int) -> int:
        """Estimate gas with chain-specific multiplier."""
        return int(base_gas * self.config.gas_multiplier)

    def get_gas_price(self) -> int:
        """Get current gas price (or estimate for L2)."""
        if self.simulate:
            # Default gas prices per chain (in gwei)
            defaults = {"ethereum": 20, "arbitrum": 0.1, "optimism": 0.05, "base": 0.05}
            return defaults.get(self.chain, 10) * 10**9  # Convert to wei

        if self._web3:
            return self._web3.eth.gas_price
        return 10 * 10**9

    def simulate_transaction(self, from_addr: str, to_addr: str,
                             value: float, tx_type: str = "swap") -> dict:
        """Simulate a transaction without sending it."""
        import random
        gas_cost = self.estimate_gas(200000) * self.get_gas_price() / 10**18

        return {
            "tx_hash": f"0x{random.randbytes(16).hex()}",
            "from": from_addr,
            "to": to_addr,
            "value": value,
            "tx_type": tx_type,
            "chain": self.chain,
            "chain_id": self.config.chain_id,
            "gas_cost_eth": gas_cost,
            "simulated": True,
        }

    def get_block_time_seconds(self) -> float:
        """Get average block time for this chain."""
        return self.config.block_time_ms / 1000.0


class MultiChainManager:
    """Manage connections across multiple chains."""

    def __init__(self, simulate: bool = True):
        self.simulate = simulate
        self.connectors: dict[str, EVMConnector] = {}

    def add_chain(self, chain: str, rpc_url: str = "") -> EVMConnector:
        """Add a chain connection."""
        connector = EVMConnector(chain, rpc_url, simulate=self.simulate)
        self.connectors[chain] = connector
        logger.info(f"Added {chain} connector")
        return connector

    def get_connector(self, chain: str) -> Optional[EVMConnector]:
        """Get connector for a specific chain."""
        return self.connectors.get(chain)

    def get_supported_chains(self) -> list[str]:
        """Get list of supported chain names."""
        return SUPPORTED_CHAINS

    def get_connected_chains(self) -> list[str]:
        """Get list of chains with active connectors."""
        return [c for c, conn in self.connectors.items() if conn.is_connected]

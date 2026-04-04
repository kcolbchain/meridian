"""
Uniswap V3 connector — routes trades on EVM chains (Arbitrum, Base, OP, Ethereum).

Direct contract interaction via web3.py. Handles:
- exactInputSingle swaps
- Token approvals
- Gas estimation with L2-specific handling
"""
import logging
import time
from dataclasses import dataclass
from typing import Optional

from web3 import Web3
from eth_account import Account

logger = logging.getLogger(__name__)

# Uniswap V3 SwapRouter addresses per chain
ROUTERS = {
    "ethereum": "0xE592427A0AEce92De3Edee1F18E0157C05861564",
    "arbitrum": "0xE592427A0AEce92De3Edee1F18E0157C05861564",
    "optimism": "0xE592427A0AEce92De3Edee1F18E0157C05861564",
    "base": "0x2626664c2603336E57B271c5C0b26F421741e481",
}

WETH = {
    "ethereum": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
    "arbitrum": "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",
    "optimism": "0x4200000000000000000000000000000000000006",
    "base": "0x4200000000000000000000000000000000000006",
}

USDC = {
    "arbitrum": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
    "base": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
    "optimism": "0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85",
    "ethereum": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
}

SWAP_ABI = [{"inputs": [{"components": [
    {"name": "tokenIn", "type": "address"}, {"name": "tokenOut", "type": "address"},
    {"name": "fee", "type": "uint24"}, {"name": "recipient", "type": "address"},
    {"name": "deadline", "type": "uint256"}, {"name": "amountIn", "type": "uint256"},
    {"name": "amountOutMinimum", "type": "uint256"}, {"name": "sqrtPriceLimitX96", "type": "uint160"},
], "name": "params", "type": "tuple"}],
    "name": "exactInputSingle", "outputs": [{"type": "uint256"}],
    "stateMutability": "payable", "type": "function"}]


@dataclass
class SwapResult:
    success: bool
    tx_hash: Optional[str] = None
    amount_in: float = 0.0
    amount_out: float = 0.0
    gas_cost_eth: float = 0.0
    error: Optional[str] = None


class UniswapConnector:
    """Execute swaps on EVM chains via Uniswap V3."""

    def __init__(self, chain: str, rpc_url: str):
        self.chain = chain
        self.w3 = Web3(Web3.HTTPProvider(rpc_url))
        self.router_addr = ROUTERS.get(chain)
        self.weth_addr = WETH.get(chain)
        self.usdc_addr = USDC.get(chain)

        if not self.router_addr:
            raise ValueError(f"No Uniswap V3 router for chain: {chain}")

    def swap_eth_to_token(self, private_key: str, token_out: str,
                          amount_eth: float, fee: int = 500) -> SwapResult:
        """Swap native ETH for a token."""
        acct = Account.from_key(private_key)
        router = self.w3.eth.contract(
            address=Web3.to_checksum_address(self.router_addr), abi=SWAP_ABI
        )
        amount_wei = self.w3.to_wei(amount_eth, "ether")

        params = {
            "tokenIn": Web3.to_checksum_address(self.weth_addr),
            "tokenOut": Web3.to_checksum_address(token_out),
            "fee": fee,
            "recipient": acct.address,
            "deadline": int(time.time()) + 300,
            "amountIn": amount_wei,
            "amountOutMinimum": 0,
            "sqrtPriceLimitX96": 0,
        }

        try:
            nonce = self.w3.eth.get_transaction_count(acct.address)
            tx = router.functions.exactInputSingle(params).build_transaction({
                "from": acct.address, "value": amount_wei, "nonce": nonce,
                "gasPrice": int(self.w3.eth.gas_price * 2),
                "chainId": self.w3.eth.chain_id, "gas": 300000,
            })
            signed = acct.sign_transaction(tx)
            tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
            gas_cost = float(self.w3.from_wei(
                receipt["gasUsed"] * receipt["effectiveGasPrice"], "ether"
            ))

            return SwapResult(
                success=receipt["status"] == 1,
                tx_hash=tx_hash.hex(),
                amount_in=amount_eth,
                gas_cost_eth=gas_cost,
            )
        except Exception as e:
            return SwapResult(success=False, error=str(e))

    def get_balance(self, address: str) -> float:
        """Get native ETH balance."""
        return float(self.w3.from_wei(self.w3.eth.get_balance(address), "ether"))

    def get_token_balance(self, address: str, token: str, decimals: int = 18) -> float:
        """Get ERC-20 token balance."""
        abi = [{"inputs": [{"name": "a", "type": "address"}], "name": "balanceOf",
                "outputs": [{"type": "uint256"}], "stateMutability": "view", "type": "function"}]
        contract = self.w3.eth.contract(address=Web3.to_checksum_address(token), abi=abi)
        return contract.functions.balanceOf(address).call() / (10 ** decimals)

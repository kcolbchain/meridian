"""Uniswap V3 execution connector — swap through UniswapV3 Router on any EVM chain."""

import logging
from typing import Optional
from web3 import Web3
from web3.contract import Contract

from .base_executor import BaseExecutor, SwapParams, SwapResult

logger = logging.getLogger(__name__)

# Uniswap V3 SwapRouter02 addresses per chain
ROUTER_ADDRESSES = {
    "ethereum": "0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45",
    "arbitrum": "0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45",
    "optimism": "0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45",
    "base": "0x2626664c2603336E57B271c5C0b26F421741e481",
    "polygon": "0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45",
}

# Minimal ABI for SwapRouter02.exactInputSingle
SWAP_ROUTER_ABI = [
    {
        "inputs": [
            {
                "components": [
                    {"name": "tokenIn", "type": "address"},
                    {"name": "tokenOut", "type": "address"},
                    {"name": "fee", "type": "uint24"},
                    {"name": "recipient", "type": "address"},
                    {"name": "amountIn", "type": "uint256"},
                    {"name": "amountOutMinimum", "type": "uint256"},
                    {"name": "sqrtPriceLimitX96", "type": "uint160"},
                ],
                "name": "params",
                "type": "tuple",
            }
        ],
        "name": "exactInputSingle",
        "outputs": [{"name": "amountOut", "type": "uint256"}],
        "stateMutability": "payable",
        "type": "function",
    }
]

ERC20_APPROVE_ABI = [
    {
        "inputs": [
            {"name": "spender", "type": "address"},
            {"name": "amount", "type": "uint256"},
        ],
        "name": "approve",
        "outputs": [{"name": "", "type": "bool"}],
        "stateMutability": "nonpayable",
        "type": "function",
    }
]


class UniswapV3Executor(BaseExecutor):
    """Execute swaps on Uniswap V3 SwapRouter02."""

    def __init__(self, w3: Web3, chain: str, private_key: str, fee_tier: int = 3000):
        self.w3 = w3
        self.chain = chain
        self.private_key = private_key
        self.account = w3.eth.account.from_key(private_key)
        self.fee_tier = fee_tier  # 500 (0.05%), 3000 (0.3%), 10000 (1%)

        router_addr = ROUTER_ADDRESSES.get(chain)
        if not router_addr:
            raise ValueError(f"No Uniswap V3 router for chain: {chain}")

        self.router: Contract = w3.eth.contract(
            address=Web3.to_checksum_address(router_addr),
            abi=SWAP_ROUTER_ABI,
        )
        self.router_address = router_addr

    async def swap(self, params: SwapParams) -> SwapResult:
        """Execute an exactInputSingle swap."""
        swap_params = {
            "tokenIn": Web3.to_checksum_address(params.token_in),
            "tokenOut": Web3.to_checksum_address(params.token_out),
            "fee": self.fee_tier,
            "recipient": self.account.address,
            "amountIn": params.amount_in,
            "amountOutMinimum": params.min_amount_out,
            "sqrtPriceLimitX96": 0,  # no price limit
        }

        tx = self.router.functions.exactInputSingle(swap_params).build_transaction({
            "from": self.account.address,
            "nonce": self.w3.eth.get_transaction_count(self.account.address),
            "gas": 300000,
            "maxFeePerGas": self.w3.eth.gas_price * 2,
            "maxPriorityFeePerGas": self.w3.to_wei(1, "gwei"),
        })

        signed = self.w3.eth.account.sign_transaction(tx, self.private_key)
        tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)

        logger.info(f"Swap executed: {tx_hash.hex()} | gas: {receipt['gasUsed']}")

        return SwapResult(
            tx_hash=tx_hash.hex(),
            amount_in=params.amount_in,
            amount_out=0,  # parse from logs in production
            gas_used=receipt["gasUsed"],
            gas_price=receipt.get("effectiveGasPrice", 0),
            success=receipt["status"] == 1,
            chain=self.chain,
            dex="uniswap_v3",
        )

    async def get_quote(self, token_in: str, token_out: str, amount_in: int) -> int:
        """Get expected output — uses Quoter contract in production."""
        # Placeholder: in production, call QuoterV2.quoteExactInputSingle
        logger.warning("get_quote not yet implemented — returning 0")
        return 0

    async def approve_token(self, token: str, spender: str, amount: int) -> str:
        """Approve router to spend tokens."""
        contract = self.w3.eth.contract(
            address=Web3.to_checksum_address(token),
            abi=ERC20_APPROVE_ABI,
        )
        tx = contract.functions.approve(
            Web3.to_checksum_address(spender), amount
        ).build_transaction({
            "from": self.account.address,
            "nonce": self.w3.eth.get_transaction_count(self.account.address),
            "gas": 60000,
        })
        signed = self.w3.eth.account.sign_transaction(tx, self.private_key)
        tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        self.w3.eth.wait_for_transaction_receipt(tx_hash)
        return tx_hash.hex()

"""MeridianVault executor — routes trades through the on-chain vault contract."""

import json
import logging
from pathlib import Path
from typing import Optional
from web3 import Web3

from .base_executor import BaseExecutor, SwapParams, SwapResult

logger = logging.getLogger(__name__)


class VaultExecutor(BaseExecutor):
    """Execute trades through the MeridianVault contract.

    The vault holds assets and the agent (owner) instructs it to trade
    via approved DEX routers. This is the production execution path.
    """

    def __init__(self, w3: Web3, vault_address: str, private_key: str,
                 chain: str = "base"):
        self.w3 = w3
        self.chain = chain
        self.private_key = private_key
        self.account = w3.eth.account.from_key(private_key)
        self.vault_address = Web3.to_checksum_address(vault_address)

        # Load vault ABI — in production, this comes from compiled artifacts
        self.vault_abi = [
            {
                "inputs": [
                    {"name": "router", "type": "address"},
                    {"name": "tokenIn", "type": "address"},
                    {"name": "tokenOut", "type": "address"},
                    {"name": "amountIn", "type": "uint256"},
                    {"name": "minAmountOut", "type": "uint256"},
                    {"name": "data", "type": "bytes"},
                ],
                "name": "executeTrade",
                "outputs": [{"name": "amountOut", "type": "uint256"}],
                "stateMutability": "nonpayable",
                "type": "function",
            },
            {
                "inputs": [{"name": "token", "type": "address"}],
                "name": "balanceOf",
                "outputs": [{"name": "", "type": "uint256"}],
                "stateMutability": "view",
                "type": "function",
            },
        ]
        self.vault = w3.eth.contract(address=self.vault_address, abi=self.vault_abi)

    async def swap(self, params: SwapParams) -> SwapResult:
        """Execute trade through vault's executeTrade function."""
        # Build the inner DEX swap calldata
        # In production, this is built by the specific DEX executor
        router_calldata = b""  # placeholder — built by DEX-specific encoder

        tx = self.vault.functions.executeTrade(
            "0x0000000000000000000000000000000000000000",  # router — set per trade
            Web3.to_checksum_address(params.token_in),
            Web3.to_checksum_address(params.token_out),
            params.amount_in,
            params.min_amount_out,
            router_calldata,
        ).build_transaction({
            "from": self.account.address,
            "nonce": self.w3.eth.get_transaction_count(self.account.address),
            "gas": 500000,
        })

        signed = self.w3.eth.account.sign_transaction(tx, self.private_key)
        tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)

        return SwapResult(
            tx_hash=tx_hash.hex(),
            amount_in=params.amount_in,
            amount_out=0,  # parse from event logs
            gas_used=receipt["gasUsed"],
            gas_price=receipt.get("effectiveGasPrice", 0),
            success=receipt["status"] == 1,
            chain=self.chain,
            dex="meridian_vault",
        )

    async def get_quote(self, token_in: str, token_out: str, amount_in: int) -> int:
        """Get vault balance of a token."""
        balance = self.vault.functions.balanceOf(
            Web3.to_checksum_address(token_out)
        ).call()
        return balance

    async def approve_token(self, token: str, spender: str, amount: int) -> str:
        """Not needed — vault manages its own approvals."""
        return "0x0"

    async def vault_balance(self, token: str) -> int:
        """Get vault's balance of a specific token."""
        return self.vault.functions.balanceOf(
            Web3.to_checksum_address(token)
        ).call()

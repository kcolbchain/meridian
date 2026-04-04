"""Base execution interface — agents call this to interact with DEXes on-chain."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional
import logging

logger = logging.getLogger(__name__)


@dataclass
class SwapParams:
    token_in: str       # Token address to sell
    token_out: str      # Token address to buy
    amount_in: int      # Amount in wei
    min_amount_out: int # Slippage protection
    deadline: int       # Unix timestamp


@dataclass
class SwapResult:
    tx_hash: str
    amount_in: int
    amount_out: int
    gas_used: int
    gas_price: int
    success: bool
    chain: str
    dex: str


class BaseExecutor(ABC):
    """Abstract base for on-chain execution across DEXes."""

    @abstractmethod
    async def swap(self, params: SwapParams) -> SwapResult:
        """Execute a token swap."""
        ...

    @abstractmethod
    async def get_quote(self, token_in: str, token_out: str, amount_in: int) -> int:
        """Get expected output amount for a swap (no execution)."""
        ...

    @abstractmethod
    async def approve_token(self, token: str, spender: str, amount: int) -> str:
        """Approve a spender to use tokens."""
        ...

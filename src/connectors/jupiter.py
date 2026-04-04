"""
Jupiter DEX aggregator connector — routes trades on Solana.

Jupiter finds the best route across all Solana DEXes (Raydium, Orca, Meteora, etc.)
and executes in a single transaction. This is the Solana equivalent of 1inch/Paraswap.

Adapted from MMAGI's TradingEngine for the meridian agent framework.
"""
import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

JUPITER_QUOTE_URL = "https://quote-api.jup.ag/v6/quote"
JUPITER_SWAP_URL = "https://quote-api.jup.ag/v6/swap"

# Common Solana token mints
SOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDT_MINT = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"


@dataclass
class JupiterQuote:
    input_mint: str
    output_mint: str
    in_amount: int
    out_amount: int
    price_impact_pct: float
    route_plan: list


@dataclass
class SwapResult:
    success: bool
    tx_signature: Optional[str] = None
    in_amount: float = 0.0
    out_amount: float = 0.0
    price_impact: float = 0.0
    error: Optional[str] = None


class JupiterConnector:
    """Execute swaps on Solana via Jupiter aggregator."""

    def __init__(self, rpc_url: str = "https://api.mainnet-beta.solana.com"):
        self.rpc_url = rpc_url

    async def get_quote(self, input_mint: str, output_mint: str,
                        amount_lamports: int, slippage_bps: int = 50) -> Optional[JupiterQuote]:
        """Get a swap quote from Jupiter."""
        params = {
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": str(amount_lamports),
            "slippageBps": slippage_bps,
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(JUPITER_QUOTE_URL, params=params,
                                       timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        logger.error(f"Jupiter quote failed: {resp.status}")
                        return None
                    data = await resp.json()

                    return JupiterQuote(
                        input_mint=data["inputMint"],
                        output_mint=data["outputMint"],
                        in_amount=int(data["inAmount"]),
                        out_amount=int(data["outAmount"]),
                        price_impact_pct=float(data.get("priceImpactPct", 0)),
                        route_plan=data.get("routePlan", []),
                    )
        except Exception as e:
            logger.error(f"Jupiter quote error: {e}")
            return None

    async def execute_swap(self, quote: JupiterQuote, wallet_pubkey: str,
                           private_key_bytes: bytes) -> SwapResult:
        """Execute a swap using a Jupiter quote.

        NOTE: This builds the transaction via Jupiter API. The actual signing
        and submission requires solders/solana-py. For the agent framework,
        we get the swap transaction from Jupiter and sign locally.
        """
        try:
            async with aiohttp.ClientSession() as session:
                swap_body = {
                    "quoteResponse": {
                        "inputMint": quote.input_mint,
                        "outputMint": quote.output_mint,
                        "inAmount": str(quote.in_amount),
                        "outAmount": str(quote.out_amount),
                        "priceImpactPct": str(quote.price_impact_pct),
                        "routePlan": quote.route_plan,
                    },
                    "userPublicKey": wallet_pubkey,
                    "wrapAndUnwrapSol": True,
                }

                async with session.post(JUPITER_SWAP_URL, json=swap_body,
                                        timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        return SwapResult(success=False, error=f"Jupiter swap API: {error_text[:200]}")

                    data = await resp.json()
                    swap_tx_b64 = data.get("swapTransaction")

                    if not swap_tx_b64:
                        return SwapResult(success=False, error="No swap transaction returned")

                    # Sign and submit the transaction
                    tx_sig = await self._sign_and_submit(swap_tx_b64, private_key_bytes)

                    return SwapResult(
                        success=tx_sig is not None,
                        tx_signature=tx_sig,
                        in_amount=quote.in_amount / 1e9,  # lamports to SOL
                        out_amount=quote.out_amount / 1e6,  # assuming USDC (6 decimals)
                        price_impact=quote.price_impact_pct,
                    )

        except Exception as e:
            return SwapResult(success=False, error=str(e))

    async def _sign_and_submit(self, swap_tx_b64: str, private_key_bytes: bytes) -> Optional[str]:
        """Sign a Jupiter swap transaction and submit to Solana."""
        try:
            import base64
            from solders.keypair import Keypair
            from solders.transaction import VersionedTransaction
            from solana.rpc.async_api import AsyncClient

            tx_bytes = base64.b64decode(swap_tx_b64)
            tx = VersionedTransaction.from_bytes(tx_bytes)

            keypair = Keypair.from_bytes(private_key_bytes)
            tx.sign([keypair])

            async with AsyncClient(self.rpc_url) as client:
                result = await client.send_transaction(tx)
                sig = str(result.value)
                logger.info(f"Solana tx submitted: {sig[:20]}...")
                return sig

        except ImportError:
            logger.warning("solders/solana-py not installed — cannot submit Solana txns")
            return None
        except Exception as e:
            logger.error(f"Solana tx failed: {e}")
            return None

    async def get_price(self, token_mint: str, vs_mint: str = USDC_MINT) -> Optional[float]:
        """Get token price in USDC via Jupiter quote (1 unit)."""
        # Quote 1 SOL worth
        amount = 1_000_000_000 if token_mint == SOL_MINT else 1_000_000
        quote = await self.get_quote(token_mint, vs_mint, amount)
        if quote:
            return quote.out_amount / 1e6  # USDC has 6 decimals
        return None

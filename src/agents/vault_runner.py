"""
vault_runner.py — Meridian Agent Vault Runner

Tick the agent's market-making loop with the ERC-4626 vault as the
source-of-truth for inventory. Depositors' assets are held in the vault;
the agent (owner) manages trading while depositors hold vault shares.

Usage:
    python -m src.agents.vault_runner --vault <VAULT_ADDRESS> --chain base

Environment:
    PRIVATE_KEY   — agent's wallet private key
    RPC_URL       — EVM RPC endpoint
    VAULT_ADDRESS — deployed MeridianVault contract address
"""

import argparse
import logging
import time
from dataclasses import dataclass
from typing import Optional

from eth_account import Account
from web3 import Web3
from web3.contract import Contract

from ..execution.vault_executor import VaultExecutor
from ..strategies.adaptive_spread import AdaptiveSpreadStrategy
from ..risk.risk_manager import RiskManager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@dataclass
class VaultState:
    total_deposits: int
    total_shares: int
    agent_balance: int
    paused: bool


class VaultRunner:
    """
    Agent loop that:
    1. Reads vault inventory (totalAssets, share price)
    2. Calculates safe position sizes
    3. Executes market-making trades via VaultExecutor
    4. Reports performance to depositors
    """

    def __init__(
        self,
        vault: Contract,
        executor: VaultExecutor,
        strategy: AdaptiveSpreadStrategy,
        risk_manager: RiskManager,
        account: Account,
        w3: Web3,
        poll_interval: int = 15,
    ):
        self.vault = vault
        self.executor = executor
        self.strategy = strategy
        self.risk = risk_manager
        self.account = account
        self.w3 = w3
        self.poll_interval = poll_interval
        self.running = False

    def get_vault_state(self) -> VaultState:
        """Fetch current vault state from chain."""
        total_assets = self.vault.functions.totalAssets().call()
        total_shares = self.vault.functions.totalSupply().call()
        agent_balance = self.vault.functions.balanceOf(self.account.address).call()
        paused = self.vault.functions.paused().call()

        return VaultState(
            total_deposits=total_assets,
            total_shares=total_shares,
            agent_balance=agent_balance,
            paused=paused,
        )

    def calculate_safe_position_size(self, vault_state: VaultState) -> int:
        """
        Calculate max safe position size per trade.
        Rule: never risk more than 5% of vault AUM in a single trade.
        """
        max_trade_pct = 0.05
        return int(vault_state.total_deposits * max_trade_pct)

    def tick(self) -> bool:
        """
        Run one agent loop iteration.
        Returns True if a trade was executed, False otherwise.
        """
        if self.running:
            logger.warning("Tick called while already running — skipping")
            return False

        self.running = True
        try:
            state = self.get_vault_state()

            if state.paused:
                logger.info("Vault is paused — skipping tick")
                return False

            if state.total_deposits == 0:
                logger.info("No deposits yet — skipping tick")
                return False

            # Get risk assessment
            risk_ok, risk_msg = self.risk.check_risk(state.total_deposits)
            if not risk_ok:
                logger.warning(f"Risk check failed: {risk_msg}")
                # Trigger circuit breaker if risk is too high
                self._emergency_pause()
                return False

            # Calculate position size
            max_size = self.calculate_safe_position_size(state)

            # Get spread from strategy
            spread = self.strategy.get_spread(state.total_deposits)
            logger.info(
                f"Vault state: deposits={state.total_deposits}, shares={state.total_shares}, "
                f"spread={spread:.4f}, max_pos={max_size}"
            )

            # Strategy generates trade signals
            signals = self.strategy.generate_signals(
                vault_assets=state.total_deposits,
                max_position=max_size,
            )

            for signal in signals:
                if not signal.get("ready"):
                    continue

                logger.info(f"Executing trade: {signal}")
                try:
                    result = self.executor.swap(
                        token_in=signal["token_in"],
                        token_out=signal["token_out"],
                        amount_in=signal["amount_in"],
                        min_amount_out=signal["min_amount_out"],
                        slippage_bps=int(spread * 10000),
                    )
                    logger.info(f"Trade result: {result}")
                except Exception as e:
                    logger.error(f"Trade failed: {e}")

            return True

        except Exception as e:
            logger.exception(f"Tick failed: {e}")
            return False
        finally:
            self.running = False

    def _emergency_pause(self):
        """Trigger circuit breaker if risk check fails critically."""
        logger.critical("EMERGENCY PAUSE — risk threshold exceeded")
        tx = self.vault.functions.setCircuitBreaker(True).build_transaction({
            "from": self.account.address,
            "nonce": self.w3.eth.get_transaction_count(self.account.address),
            "gas": 100000,
        })
        signed = self.w3.eth.account.sign_transaction(tx, self.account.key)
        tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        self.w3.eth.wait_for_transaction_receipt(tx_hash)
        logger.info("Circuit breaker activated")

    def run(self):
        """Main agent loop."""
        logger.info("Vault runner started")
        while True:
            try:
                self.tick()
            except KeyboardInterrupt:
                logger.info("Shutdown signal received")
                break
            except Exception as e:
                logger.exception(f"Unexpected error: {e}")

            time.sleep(self.poll_interval)


def main():
    parser = argparse.ArgumentParser(description="Meridian Vault Agent Runner")
    parser.add_argument("--vault", required=True, help="Vault contract address")
    parser.add_argument("--chain", default="base", help="Chain name (base, ethereum, etc.)")
    parser.add_argument("--poll-interval", type=int, default=15, help="Tick interval in seconds")
    args = parser.parse_args()

    rpc_url = "https://base-mainnet.public.blastapi.io"  # TODO: configurable
    private_key = os.environ.get("PRIVATE_KEY")
    if not private_key:
        raise ValueError("PRIVATE_KEY env var not set")

    w3 = Web3(Web3.HTTPProvider(rpc_url))
    account = Account.from_key(private_key)

    # Load vault contract
    vault_address = Web3.to_checksum_address(args.vault)
    with open("src/execution/vault_executor.py") as f:
        # Use vault_executor ABI
        pass  # In production, load from compiled artifact

    logger.info(f"Agent address: {account.address}")
    logger.info(f"Vault address: {vault_address}")

    # Initialise components
    # vault = w3.eth.contract(address=vault_address, abi=VAULT_ABI)
    # executor = VaultExecutor(w3, vault_address, private_key, args.chain)
    # strategy = AdaptiveSpreadStrategy(w3)
    # risk = RiskManager(w3)
    # runner = VaultRunner(vault, executor, strategy, risk, account, w3, args.poll_interval)
    # runner.run()


if __name__ == "__main__":
    import os
    main()

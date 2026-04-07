import os
import time
from typing import Dict, Optional

from web3 import Web3
from web3.exceptions import ContractCustomError, ContractLogicError, TransactionNotFound
from eth_typing.abi import ABIFunction

# Simplified ABI for Chainlink AggregatorV3Interface
# Contains only the methods required: latestRoundData, decimals, description.
CHAINLINK_ABI = [
    {
        "inputs": [],
        "name": "decimals",
        "outputs": [{"internalType": "uint8", "name": "", "type": "uint8"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "description",
        "outputs": [{"internalType": "string", "name": "", "type": "string"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "latestRoundData",
        "outputs": [
            {"internalType": "uint80", "name": "roundId", "type": "uint80"},
            {"internalType": "int256", "name": "answer", "type": "int256"},
            {"internalType": "uint256", "name": "startedAt", "type": "uint256"},
            {"internalType": "uint256", "name": "updatedAt", "name": "type": "uint256"},
            {"internalType": "uint80", "name": "answeredInRound", "name": "type": "uint80"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
]

class OracleError(Exception):
    """Base exception for oracle-related errors."""
    pass

class OracleConnectionError(OracleError):
    """Raised when the connection to the oracle provider fails."""
    pass

class OracleFeedNotFound(OracleError):
    """Raised when an asset pair's feed address is not configured or invalid."""
    pass

class OracleStalePriceError(OracleError):
    """Raised when the fetched price is considered stale."""
    pass

class ChainlinkOracle:
    """
    Fetches real-time prices from Chainlink Data Feeds using web3.py.
    """

    def __init__(
        self,
        provider_url: str,
        feed_addresses: Dict[str, str],
        heartbeat_threshold_seconds: int = 3600,  # 1 hour
    ):
        """
        Initializes the ChainlinkOracle.

        Args:
            provider_url: The URL of the Ethereum RPC provider (e.g., Infura, Alchemy).
            feed_addresses: A dictionary mapping asset pair names (e.g., "ETH/USD")
                            to their Chainlink AggregatorV3Interface contract addresses.
            heartbeat_threshold_seconds: Maximum time in seconds since the last update
                                         before a price is considered stale.
        """
        if not provider_url:
            raise ValueError("Provider URL cannot be empty.")
        if not feed_addresses:
            raise ValueError("Feed addresses cannot be empty.")

        self.provider_url = provider_url
        self.feed_addresses = {k.upper(): v for k, v in feed_addresses.items()} # Normalize keys
        self.heartbeat_threshold_seconds = heartbeat_threshold_seconds
        self._w3: Optional[Web3] = None
        self._contracts: Dict[str, any] = {} # Store initialized Contract objects
        self._decimals: Dict[str, int] = {} # Cache decimals for each feed

    @property
    def w3(self) -> Web3:
        """Lazily initialize web3 connection."""
        if self._w3 is None or not self._w3.is_connected():
            try:
                self._w3 = Web3(Web3.HTTPProvider(self.provider_url))
                if not self._w3.is_connected():
                    raise ConnectionError("web3.py failed to connect to provider.")
            except Exception as e:
                self._w3 = None # Reset to force reconnection attempt next time
                raise OracleConnectionError(f"Could not connect to RPC provider at {self.provider_url}: {e}") from e
        return self._w3

    def _get_contract(self, asset_pair: str) -> any: # web3.contract.Contract
        """Gets or initializes the contract instance for a given asset pair."""
        if asset_pair not in self._contracts:
            feed_address = self.feed_addresses.get(asset_pair)
            if not feed_address:
                raise OracleFeedNotFound(f"No Chainlink feed address configured for asset pair: {asset_pair}")
            try:
                checksum_address = self.w3.to_checksum_address(feed_address)
                self._contracts[asset_pair] = self.w3.eth.contract(address=checksum_address, abi=CHAINLINK_ABI)
            except Exception as e:
                raise OracleFeedNotFound(f"Invalid Chainlink feed address '{feed_address}' for {asset_pair}: {e}") from e
        return self._contracts[asset_pair]

    def _get_decimals(self, asset_pair: str, contract: any) -> int:
        """Gets or caches the decimals for a given feed contract."""
        if asset_pair not in self._decimals:
            try:
                self._decimals[asset_pair] = contract.functions.decimals().call()
            except Exception as e:
                raise OracleError(f"Could not fetch decimals for {asset_pair}: {e}") from e
        return self._decimals[asset_pair]

    def get_price(self, asset_pair: str) -> float:
        """
        Fetches the current price for a given asset pair from Chainlink.

        Args:
            asset_pair: The name of the asset pair (e.g., "ETH/USD").

        Returns:
            The current price as a float.

        Raises:
            OracleConnectionError: If connection to RPC provider fails.
            OracleFeedNotFound: If the asset pair is not configured or feed address is invalid.
            OracleStalePriceError: If the fetched price is older than the heartbeat threshold.
            OracleError: For other unexpected errors during price fetching.
        """
        asset_pair = asset_pair.upper() # Normalize input

        try:
            contract = self._get_contract(asset_pair)
            decimals = self._get_decimals(asset_pair, contract)

            # Fetch latest round data
            round_data = contract.functions.latestRoundData().call()
            # round_data structure: (roundId, answer, startedAt, updatedAt, answeredInRound)
            price_raw = round_data[1]
            updated_at = round_data[3]

            if price_raw <= 0:
                raise OracleError(f"Invalid price ({price_raw}) fetched for {asset_pair}")

            # Stale price check
            current_timestamp = int(time.time())
            if current_timestamp - updated_at > self.heartbeat_threshold_seconds:
                raise OracleStalePriceError(
                    f"Price for {asset_pair} is stale. Last updated {updated_at} "
                    f"({current_timestamp - updated_at}s ago), threshold is {self.heartbeat_threshold_seconds}s."
                )

            return float(price_raw) / (10**decimals)

        except (OracleConnectionError, OracleFeedNotFound, OracleStalePriceError) as e:
            # Re-raise specific oracle errors
            raise e
        except (ContractCustomError, ContractLogicError, TransactionNotFound) as e:
            # Specific web3.py contract errors
            raise OracleError(f"Chainlink contract call failed for {asset_pair}: {e}") from e
        except Exception as e:
            # Catch any other unexpected errors
            raise OracleError(f"An unexpected error occurred while fetching price for {asset_pair}: {e}") from e

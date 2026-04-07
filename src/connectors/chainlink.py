import time
from typing import Dict, Optional

from web3 import Web3
from web3.exceptions import ContractCustomError, ContractLogicError, TransactionNotFound

# Minimal ABI for Chainlink AggregatorV3Interface
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
            {"internalType": "uint256", "name": "updatedAt", "type": "uint256"},
            {"internalType": "uint80", "name": "answeredInRound", "type": "uint80"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
]


class OracleError(Exception):
    """Base exception for oracle-related errors."""


class OracleConnectionError(OracleError):
    """Raised when the connection to the oracle provider fails."""


class OracleFeedNotFound(OracleError):
    """Raised when an asset pair's feed address is not configured or invalid."""


class OracleStalePriceError(OracleError):
    """Raised when the fetched price is considered stale."""


class ChainlinkOracle:
    """Fetches real-time prices from Chainlink Data Feeds using web3.py."""

    def __init__(
        self,
        provider_url: str,
        feed_addresses: Dict[str, str],
        heartbeat_threshold_seconds: int = 3600,
    ):
        """
        Args:
            provider_url: Ethereum RPC provider URL (Infura, Alchemy, public RPC, etc.)
            feed_addresses: Mapping of asset pair name to Chainlink feed contract address.
                            E.g. {"ETH/USD": "0x5f4eC3..."}
            heartbeat_threshold_seconds: Max seconds since last update before price is
                            considered stale. Default 3600 (1 hour).
        """
        if not provider_url:
            raise ValueError("provider_url cannot be empty")
        if not feed_addresses:
            raise ValueError("feed_addresses cannot be empty")
        self.provider_url = provider_url
        self.feed_addresses = {k.upper(): v for k, v in feed_addresses.items()}
        self.heartbeat_threshold_seconds = heartbeat_threshold_seconds
        self._w3: Optional[Web3] = None
        self._contracts: Dict[str, object] = {}
        self._decimals: Dict[str, int] = {}

    @property
    def w3(self) -> Web3:
        if self._w3 is None or not self._w3.is_connected():
            try:
                self._w3 = Web3(Web3.HTTPProvider(self.provider_url))
                if not self._w3.is_connected():
                    raise ConnectionError("Could not connect to provider")
            except Exception as exc:
                self._w3 = None
                raise OracleConnectionError(
                    f"Failed to connect to {self.provider_url}: {exc}"
                ) from exc
        return self._w3

    def _get_contract(self, asset_pair: str) -> object:
        if asset_pair not in self._contracts:
            address = self.feed_addresses.get(asset_pair)
            if not address:
                raise OracleFeedNotFound(
                    f"No feed configured for {asset_pair}"
                )
            try:
                checksum = self.w3.to_checksum_address(address)
                self._contracts[asset_pair] = self.w3.eth.contract(
                    address=checksum, abi=CHAINLINK_ABI
                )
            except Exception as exc:
                raise OracleFeedNotFound(
                    f"Invalid feed address '{address}' for {asset_pair}: {exc}"
                ) from exc
        return self._contracts[asset_pair]

    def _get_decimals(self, asset_pair: str, contract: object) -> int:
        if asset_pair not in self._decimals:
            try:
                self._decimals[asset_pair] = contract.functions.decimals().call()
            except Exception as exc:
                raise OracleError(f"Could not fetch decimals for {asset_pair}: {exc}") from exc
        return self._decimals[asset_pair]

    def get_price(self, asset_pair: str) -> float:
        """Fetch the latest price for an asset pair.

        Returns:
            Price as a float (adjusted for decimals).

        Raises:
            OracleConnectionError: RPC connection failed.
            OracleFeedNotFound: Asset pair not configured or address invalid.
            OracleStalePriceError: Price is older than heartbeat_threshold_seconds.
            OracleError: Any other oracle error.
        """
        asset_pair = asset_pair.upper()
        try:
            contract = self._get_contract(asset_pair)
            decimals = self._get_decimals(asset_pair, contract)
            round_data = contract.functions.latestRoundData().call()
            # round_data = (roundId, answer, startedAt, updatedAt, answeredInRound)
            price_raw, updated_at = round_data[1], round_data[3]
            if price_raw <= 0:
                raise OracleError(f"Invalid price {price_raw} for {asset_pair}")
            age = int(time.time()) - updated_at
            if age > self.heartbeat_threshold_seconds:
                raise OracleStalePriceError(
                    f"{asset_pair} price is {age}s old (threshold: {self.heartbeat_threshold_seconds}s)"
                )
            return float(price_raw) / (10 ** decimals)
        except (OracleConnectionError, OracleFeedNotFound, OracleStalePriceError, OracleError):
            raise
        except (ContractCustomError, ContractLogicError, TransactionNotFound) as exc:
            raise OracleError(f"Contract call failed for {asset_pair}: {exc}") from exc
        except Exception as exc:
            raise OracleError(f"Unexpected error fetching {asset_pair}: {exc}") from exc

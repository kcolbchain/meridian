// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "@openzeppelin/contracts/access/Ownable.sol";

/**
 * @title OracleAdapter
 * @notice Unified oracle interface for Meridian agents.
 * Supports Chainlink price feeds and on-chain TWAP from DEX pools.
 * @dev Part of kcolbchain/meridian
 */
contract OracleAdapter is Ownable {
    /// @notice Chainlink-compatible price feed interface
    struct PriceFeed {
        address feed;       // Chainlink aggregator address
        uint8 decimals;     // Feed decimals
        uint256 maxStaleness; // Max seconds before price is considered stale
        bool active;
    }

    /// @notice Registered price feeds by pair ID (e.g. keccak256("ETH/USDC"))
    mapping(bytes32 => PriceFeed) public feeds;

    /// @notice Custom prices set by the agent (for assets without Chainlink feeds)
    mapping(bytes32 => uint256) public customPrices;
    mapping(bytes32 => uint256) public customPriceTimestamps;

    event FeedRegistered(bytes32 indexed pairId, address feed, uint8 decimals);
    event CustomPriceSet(bytes32 indexed pairId, uint256 price);

    error StalePrice(bytes32 pairId, uint256 age, uint256 maxAge);
    error NoPriceFeed(bytes32 pairId);
    error InvalidPrice();

    constructor() Ownable(msg.sender) {}

    /// @notice Register a Chainlink price feed
    function registerFeed(
        string calldata pair,
        address feed,
        uint8 decimals,
        uint256 maxStaleness
    ) external onlyOwner {
        bytes32 pairId = keccak256(bytes(pair));
        feeds[pairId] = PriceFeed({
            feed: feed,
            decimals: decimals,
            maxStaleness: maxStaleness,
            active: true
        });
        emit FeedRegistered(pairId, feed, decimals);
    }

    /// @notice Get the latest price for a pair
    /// @return price Price with 18 decimals
    /// @return timestamp When the price was last updated
    function getPrice(string calldata pair) external view returns (uint256 price, uint256 timestamp) {
        bytes32 pairId = keccak256(bytes(pair));
        PriceFeed storage pf = feeds[pairId];

        if (pf.active && pf.feed != address(0)) {
            return _getChainlinkPrice(pairId, pf);
        }

        // Fall back to custom price
        uint256 cp = customPrices[pairId];
        uint256 ct = customPriceTimestamps[pairId];
        if (cp > 0) {
            return (cp, ct);
        }

        revert NoPriceFeed(pairId);
    }

    /// @notice Set a custom price (for assets without oracle feeds)
    function setCustomPrice(string calldata pair, uint256 price) external onlyOwner {
        if (price == 0) revert InvalidPrice();
        bytes32 pairId = keccak256(bytes(pair));
        customPrices[pairId] = price;
        customPriceTimestamps[pairId] = block.timestamp;
        emit CustomPriceSet(pairId, price);
    }

    /// @notice Compute pair ID for a given string
    function pairId(string calldata pair) external pure returns (bytes32) {
        return keccak256(bytes(pair));
    }

    function _getChainlinkPrice(bytes32 pairId, PriceFeed storage pf)
        internal view returns (uint256 price, uint256 timestamp)
    {
        // Chainlink AggregatorV3Interface.latestRoundData()
        (, int256 answer,, uint256 updatedAt,) = IChainlinkFeed(pf.feed).latestRoundData();

        uint256 age = block.timestamp - updatedAt;
        if (age > pf.maxStaleness) {
            revert StalePrice(pairId, age, pf.maxStaleness);
        }
        if (answer <= 0) revert InvalidPrice();

        // Normalize to 18 decimals
        price = uint256(answer) * (10 ** (18 - pf.decimals));
        timestamp = updatedAt;
    }
}

/// @notice Minimal Chainlink aggregator interface
interface IChainlinkFeed {
    function latestRoundData() external view returns (
        uint80 roundId,
        int256 answer,
        uint256 startedAt,
        uint256 updatedAt,
        uint80 answeredInRound
    );
}

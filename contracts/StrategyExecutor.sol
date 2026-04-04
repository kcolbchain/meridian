// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "@openzeppelin/contracts/access/Ownable.sol";
import "./MeridianVault.sol";
import "./OracleAdapter.sol";

/**
 * @title StrategyExecutor
 * @notice Coordinates between the vault, oracle, and DEX routers.
 * The agent calls this contract to execute complete market-making cycles:
 * read price → compute quotes → execute trades.
 * @dev Part of kcolbchain/meridian
 */
contract StrategyExecutor is Ownable {
    MeridianVault public vault;
    OracleAdapter public oracle;

    /// @notice Strategy parameters
    uint256 public baseSpreadBps;     // Base spread in basis points
    uint256 public maxPositionSize;   // Max position size per trade
    uint256 public rebalanceThreshold; // Inventory imbalance threshold

    /// @notice Trade history for analytics
    struct TradeRecord {
        address tokenIn;
        address tokenOut;
        uint256 amountIn;
        uint256 amountOut;
        uint256 timestamp;
        string pair;
    }

    TradeRecord[] public tradeHistory;

    /// @notice Cumulative PnL tracking per token
    mapping(address => int256) public tokenPnL;

    event StrategyUpdated(uint256 baseSpreadBps, uint256 maxPositionSize, uint256 rebalanceThreshold);
    event CycleExecuted(string pair, uint256 bidAmount, uint256 askAmount, uint256 timestamp);

    constructor(
        address _vault,
        address _oracle,
        uint256 _baseSpreadBps,
        uint256 _maxPositionSize,
        uint256 _rebalanceThreshold
    ) Ownable(msg.sender) {
        vault = MeridianVault(_vault);
        oracle = OracleAdapter(_oracle);
        baseSpreadBps = _baseSpreadBps;
        maxPositionSize = _maxPositionSize;
        rebalanceThreshold = _rebalanceThreshold;
    }

    /// @notice Update strategy parameters
    function updateStrategy(
        uint256 _baseSpreadBps,
        uint256 _maxPositionSize,
        uint256 _rebalanceThreshold
    ) external onlyOwner {
        baseSpreadBps = _baseSpreadBps;
        maxPositionSize = _maxPositionSize;
        rebalanceThreshold = _rebalanceThreshold;
        emit StrategyUpdated(_baseSpreadBps, _maxPositionSize, _rebalanceThreshold);
    }

    /// @notice Compute bid and ask prices for a pair
    /// @return bidPrice Price to buy at (18 decimals)
    /// @return askPrice Price to sell at (18 decimals)
    function computeQuotes(string calldata pair) external view returns (uint256 bidPrice, uint256 askPrice) {
        (uint256 midPrice,) = oracle.getPrice(pair);

        uint256 halfSpread = (midPrice * baseSpreadBps) / 20000;
        bidPrice = midPrice - halfSpread;
        askPrice = midPrice + halfSpread;
    }

    /// @notice Record a trade in history (called after vault.executeTrade)
    function recordTrade(
        address tokenIn,
        address tokenOut,
        uint256 amountIn,
        uint256 amountOut,
        string calldata pair
    ) external onlyOwner {
        tradeHistory.push(TradeRecord({
            tokenIn: tokenIn,
            tokenOut: tokenOut,
            amountIn: amountIn,
            amountOut: amountOut,
            timestamp: block.timestamp,
            pair: pair
        }));
    }

    /// @notice Get total number of trades executed
    function tradeCount() external view returns (uint256) {
        return tradeHistory.length;
    }

    /// @notice Get vault address
    function getVault() external view returns (address) {
        return address(vault);
    }

    /// @notice Get oracle address
    function getOracle() external view returns (address) {
        return address(oracle);
    }
}

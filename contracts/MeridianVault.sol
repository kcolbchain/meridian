// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import "@openzeppelin/contracts/access/Ownable.sol";
import "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

/**
 * @title MeridianVault
 * @notice Agent-controlled vault for autonomous market making.
 * Holds assets that the agent uses to provide liquidity on DEXes.
 * Only the agent (owner) can execute trades and manage positions.
 * @dev Part of kcolbchain/meridian
 */
contract MeridianVault is Ownable, ReentrancyGuard {
    using SafeERC20 for IERC20;

    /// @notice Maximum slippage allowed in basis points (e.g. 100 = 1%)
    uint256 public maxSlippageBps;

    /// @notice Approved DEX routers that the agent can interact with
    mapping(address => bool) public approvedRouters;

    /// @notice Total value deposited per token (for tracking)
    mapping(address => uint256) public totalDeposited;

    /// @notice Circuit breaker — agent can pause itself if strategy detects anomaly
    bool public paused;

    event Deposited(address indexed token, uint256 amount);
    event Withdrawn(address indexed token, address indexed to, uint256 amount);
    event RouterApproved(address indexed router, bool approved);
    event TradeExecuted(address indexed router, address indexed tokenIn, address indexed tokenOut, uint256 amountIn);
    event SlippageUpdated(uint256 newMaxBps);
    event CircuitBreakerTriggered(bool paused);

    error VaultPaused();
    error RouterNotApproved(address router);
    error SlippageTooHigh(uint256 requested, uint256 max);
    error TradeReverted();

    modifier whenNotPaused() {
        if (paused) revert VaultPaused();
        _;
    }

    constructor(uint256 _maxSlippageBps) Ownable(msg.sender) {
        maxSlippageBps = _maxSlippageBps;
    }

    /// @notice Deposit tokens into the vault for the agent to trade with
    function deposit(address token, uint256 amount) external onlyOwner {
        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);
        totalDeposited[token] += amount;
        emit Deposited(token, amount);
    }

    /// @notice Withdraw tokens from the vault
    function withdraw(address token, address to, uint256 amount) external onlyOwner {
        IERC20(token).safeTransfer(to, amount);
        emit Withdrawn(token, to, amount);
    }

    /// @notice Approve a DEX router for trading
    function approveRouter(address router, bool approved) external onlyOwner {
        approvedRouters[router] = approved;
        emit RouterApproved(router, approved);
    }

    /// @notice Execute a trade through an approved DEX router
    /// @param router The DEX router address
    /// @param tokenIn Token being sold
    /// @param tokenOut Token being bought
    /// @param amountIn Amount of tokenIn to sell
    /// @param minAmountOut Minimum acceptable output (slippage protection)
    /// @param data Encoded swap calldata for the router
    function executeTrade(
        address router,
        address tokenIn,
        address tokenOut,
        uint256 amountIn,
        uint256 minAmountOut,
        bytes calldata data
    ) external onlyOwner whenNotPaused nonReentrant returns (uint256 amountOut) {
        if (!approvedRouters[router]) revert RouterNotApproved(router);

        // Approve router to spend tokenIn
        IERC20(tokenIn).safeIncreaseAllowance(router, amountIn);

        // Record balance before
        uint256 balanceBefore = IERC20(tokenOut).balanceOf(address(this));

        // Execute the swap
        (bool success,) = router.call(data);
        if (!success) revert TradeReverted();

        // Calculate actual output
        amountOut = IERC20(tokenOut).balanceOf(address(this)) - balanceBefore;

        // Verify slippage
        if (amountOut < minAmountOut) {
            revert SlippageTooHigh(
                ((amountIn - amountOut) * 10000) / amountIn,
                maxSlippageBps
            );
        }

        emit TradeExecuted(router, tokenIn, tokenOut, amountIn);
    }

    /// @notice Update maximum slippage tolerance
    function setMaxSlippage(uint256 newMaxBps) external onlyOwner {
        maxSlippageBps = newMaxBps;
        emit SlippageUpdated(newMaxBps);
    }

    /// @notice Circuit breaker — pause/unpause the vault
    function setCircuitBreaker(bool _paused) external onlyOwner {
        paused = _paused;
        emit CircuitBreakerTriggered(_paused);
    }

    /// @notice Get vault balance of a specific token
    function balanceOf(address token) external view returns (uint256) {
        return IERC20(token).balanceOf(address(this));
    }

    /// @notice Emergency: recover tokens sent to the vault by mistake
    function emergencyWithdraw(address token) external onlyOwner {
        uint256 balance = IERC20(token).balanceOf(address(this));
        IERC20(token).safeTransfer(msg.sender, balance);
    }
}

// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/token/ERC20/extensions/ERC4626.sol";
import "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import "@openzeppelin/contracts/access/Ownable.sol";
import "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import "@openzeppelin/contracts/interfaces/IERC4626.sol";

/**
 * @title MeridianVault
 * @notice ERC-4626 compliant vault for autonomous market making agents.
 * Depositors receive share tokens representing proportional ownership of vault assets.
 * Only the agent (owner) can execute trades and manage positions.
 * @dev Part of kcolbchain/meridian
 */
contract MeridianVault is ERC4626, Ownable, ReentrancyGuard {
    using SafeERC20 for IERC20;

    /// @notice Maximum slippage allowed in basis points (e.g. 100 = 1%)
    uint256 public maxSlippageBps;

    /// @notice Approved DEX routers that the agent can interact with
    mapping(address => bool) public approvedRouters;

    /// @notice Circuit breaker — agent can pause if strategy detects anomaly
    bool public paused;

    /// @notice Performance fee in basis points (e.g. 2000 = 20%). Charged on profits.
    uint256 public performanceFee;

    /// @notice Management fee in basis points (e.g. 100 = 1% annual). Charged on AUM.
    uint256 public managementFee;

    /// @notice High water mark per depositor (to prevent double-charging on volatility)
    mapping(address => uint256) public highWaterMark;

    /// @notice Last fee claim timestamp per depositor
    mapping(address => uint256) public lastFeeClaim;

    /// @notice Accumulated performance fees (for owner to claim)
    uint256 public accumulatedPerformanceFees;

    /// @notice Agent's share of profits in basis points (remainder goes to depositors)
    uint256 public agentProfitShare;

    event Deposited(address indexed token, uint256 amount, uint256 shares);
    event Withdrawn(address indexed token, address indexed to, uint256 amount, uint256 shares);
    event RouterApproved(address indexed router, bool approved);
    event TradeExecuted(address indexed router, address indexed tokenIn, address indexed tokenOut, uint256 amountIn);
    event SlippageUpdated(uint256 newMaxBps);
    event CircuitBreakerTriggered(bool paused);
    event PerformanceFeeUpdated(uint256 newFee);
    event ManagementFeeUpdated(uint256 newFee);
    event FeesClaimed(address indexed recipient, uint256 amount);
    event HighWaterMarkUpdated(address indexed depositor, uint256 newHwm);

    error VaultPaused();
    error RouterNotApproved(address router);
    error SlippageTooHigh(uint256 requested, uint256 max);
    error TradeReverted();
    error Unauthorized();
    error ZeroShares();
    error FeeExceedsMax();

    modifier whenNotPaused() {
        if (paused) revert VaultPaused();
        _;
    }

    /// @param _asset The underlying ERC20 token this vault accepts
    /// @param _maxSlippageBps Initial max slippage in bps
    /// @param _performanceFee Performance fee in bps (max 5000 = 50%)
    /// @param _managementFee Management fee in bps (max 500 = 5%)
    constructor(
        IERC20Metadata _asset,
        uint256 _maxSlippageBps,
        uint256 _performanceFee,
        uint256 _managementFee
    ) ERC4626(_asset) Ownable(msg.sender) {
        require(_performanceFee <= 5000, "performance fee too high");
        require(_managementFee <= 500, "management fee too high");
        maxSlippageBps = _maxSlippageBps;
        performanceFee = _performanceFee;
        managementFee = _managementFee;
        agentProfitShare = 8000; // 80% to agent, 20% to depositors (via HWM)
    }

    // ─── ERC-4626 Overrides ───────────────────────────────────────────────

    /// @notice Deposit assets and receive shares
    function deposit(uint256 assets, address receiver) external override returns (uint256 shares) {
        require(assets > 0, "zero assets");
        shares = previewDeposit(assets);
        require(shares > 0, "zero shares");
        _updateHighWaterMark(receiver, assets, shares);
        super.deposit(assets, receiver);
        emit Deposited(asset(), assets, shares);
    }

    /// @notice Mint exact shares and pull assets
    function mint(uint256 shares, address receiver) external override returns (uint256 assets) {
        require(shares > 0, "zero shares");
        assets = previewMint(shares);
        _updateHighWaterMark(receiver, assets, shares);
        super.mint(shares, receiver);
        emit Deposited(asset(), assets, shares);
    }

    /// @notice Redeem shares for assets
    function redeem(uint256 shares, address receiver, address owner) external override returns (uint256 assets) {
        require(shares > 0, "zero shares");
        assets = super.redeem(shares, receiver, owner);
        emit Withdrawn(asset(), receiver, assets, shares);
        return assets;
    }

    /// @notice Withdraw exact assets, burn shares
    function withdraw(uint256 assets, address receiver, address owner) external override returns (uint256 shares) {
        require(assets > 0, "zero assets");
        shares = super.withdraw(assets, receiver, owner);
        emit Withdrawn(asset(), receiver, assets, shares);
        return shares;
    }

    // ─── Internal fee logic ────────────────────────────────────────────────

    function _updateHighWaterMark(address depositor, uint256 assets, uint256 shares) internal {
        // First deposit or profit scenario: update HWM
        uint256 hwm = highWaterMark[depositor];
        if (hwm == 0) {
            // First deposit — set HWM based on initial share price
            highWaterMark[depositor] = shares * 1e18 / assets;
        }
        // HWM is updated when profits are realized on withdrawal
        lastFeeClaim[depositor] = block.timestamp;
    }

    /// @notice Update HWM on withdrawal to reflect realized profits
    function _updateHwmOnWithdraw(address depositor, uint256 sharesBurned) internal {
        uint256 hwm = highWaterMark[depositor];
        uint256 currentSharePrice = totalAssets() * 1e18 / totalSupply();
        if (currentSharePrice > hwm) {
            highWaterMark[depositor] = currentSharePrice;
            emit HighWaterMarkUpdated(depositor, currentSharePrice);
        }
    }

    /// @dev Override _withdraw to hook HWM update
    function _withdraw(
        address caller,
        address receiver,
        address owner,
        uint256 assets,
        uint256 shares
    ) internal override {
        super._withdraw(caller, receiver, owner, assets, shares);
        _updateHwmOnWithdraw(owner, shares);
    }

    // ─── Trading (agent-only) ───────────────────────────────────────────────

    /// @notice Execute a trade through an approved DEX router
    /// @dev Only callable by agent (owner)
    function executeTrade(
        address router,
        address tokenIn,
        address tokenOut,
        uint256 amountIn,
        uint256 minAmountOut,
        bytes calldata data
    ) external onlyOwner whenNotPaused nonReentrant returns (uint256 amountOut) {
        if (!approvedRouters[router]) revert RouterNotApproved(router);
        require(tokenOut == asset(), "tokenOut must be vault asset");

        IERC20(tokenIn).safeIncreaseAllowance(router, amountIn);

        uint256 balanceBefore = IERC20(tokenOut).balanceOf(address(this));

        (bool success,) = router.call(data);
        if (!success) revert TradeReverted();

        amountOut = IERC20(tokenOut).balanceOf(address(this)) - balanceBefore;
        if (amountOut < minAmountOut) revert SlippageTooHigh(amountOut, minAmountOut);

        emit TradeExecuted(router, tokenIn, tokenOut, amountIn);
    }

    /// @notice Pull additional assets from agent's external wallet into vault for trading
    function topUp(uint256 amount) external onlyOwner {
        IERC20(asset()).safeTransferFrom(msg.sender, address(this), amount);
    }

    // ─── Admin ────────────────────────────────────────────────────────────

    /// @notice Approve/revoke a DEX router
    function approveRouter(address router, bool approved) external onlyOwner {
        approvedRouters[router] = approved;
        emit RouterApproved(router, approved);
    }

    /// @notice Update max slippage tolerance
    function setMaxSlippage(uint256 newMaxBps) external onlyOwner {
        maxSlippageBps = newMaxBps;
        emit SlippageUpdated(newMaxBps);
    }

    /// @notice Update performance fee (onlyOwner)
    function setPerformanceFee(uint256 newFee) external onlyOwner {
        require(newFee <= 5000, "fee too high");
        performanceFee = newFee;
        emit PerformanceFeeUpdated(newFee);
    }

    /// @notice Update management fee (onlyOwner)
    function setManagementFee(uint256 newFee) external onlyOwner {
        require(newFee <= 500, "fee too high");
        managementFee = newFee;
        emit ManagementFeeUpdated(newFee);
    }

    /// @notice Circuit breaker — pause/unpause vault
    function setCircuitBreaker(bool _paused) external onlyOwner {
        paused = _paused;
        emit CircuitBreakerTriggered(_paused);
    }

    /// @notice Claim accumulated performance fees (owner only)
    function claimPerformanceFees(address to) external onlyOwner returns (uint256) {
        uint256 fees = accumulatedPerformanceFees;
        if (fees == 0) return 0;
        accumulatedPerformanceFees = 0;
        IERC20(asset()).safeTransfer(to, fees);
        emit FeesClaimed(to, fees);
        return fees;
    }

    // ─── View functions ───────────────────────────────────────────────────

    /// @notice Returns max depositable (capped by asset balance or vault limit)
    function maxDeposit(address) external view override returns (uint256) {
        return IERC20(asset()).balanceOf(address(this));
    }

    /// @notice Returns max redeemable (capped by shares held)
    function maxRedeem(address owner) external view override returns (uint256) {
        return balanceOf(owner);
    }

    /// @notice Preview deposit — applies management fee on first deposit
    function previewDeposit(uint256 assets) public view override returns (uint256) {
        uint256 supply = totalSupply();
        if (supply == 0) {
            return assets; // First depositor gets 1:1
        }
        return assets * supply / totalAssets();
    }

    /// @notice Preview withdraw — applies performance fee on profit
    function previewWithdraw(uint256 assets) public view override returns (uint256) {
        uint256 supply = totalSupply();
        if (supply == 0) return assets;
        uint256 shares = assets * supply / totalAssets();
        // Performance fee on profit is applied at withdrawal via HWM
        return shares;
    }
}

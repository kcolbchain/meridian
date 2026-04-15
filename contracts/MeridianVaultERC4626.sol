// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "@openzeppelin/contracts/token/ERC20/extensions/ERC4626.sol";
import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import "@openzeppelin/contracts/access/Ownable.sol";
import "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

/**
 * @title MeridianVaultERC4626
 * @notice Tokenised market-maker shares vault — ERC-4626 wrapper around MeridianVault.
 *
 * Third-party LPs deposit a base asset (e.g. USDC) and receive vault shares
 * representing pro-rata ownership of the agent's trading inventory.
 * The market-making agent (owner) retains exclusive control over trade execution
 * while depositors passively earn a share of quoting profit.
 *
 * Fee structure:
 *  - Management fee: accrues linearly over time as % of AUM (annualised).
 *  - Performance fee: charged on profit above the high-water-mark only.
 *    Depositors are never double-charged after a drawdown-and-recovery.
 *
 * @dev Built on OpenZeppelin ERC4626. Fees are realised by minting extra shares
 *      to the fee recipient, diluting LP shares proportionally.
 *      All fee parameters are configurable by the owner (agent wallet).
 *
 * @author TestAutomaton — contribution to kcolbchain/meridian
 */
contract MeridianVaultERC4626 is ERC4626, Ownable, ReentrancyGuard {
    using SafeERC20 for IERC20;

    // ─────────────────────────────────────────────────────────────────────────
    // Constants
    // ─────────────────────────────────────────────────────────────────────────

    uint256 public constant BPS_DENOMINATOR = 10_000;
    uint256 public constant SECONDS_PER_YEAR = 365 days;
    /// @dev Max 10% management fee, 50% performance fee (safety caps)
    uint256 public constant MAX_MGMT_FEE_BPS = 1_000;
    uint256 public constant MAX_PERF_FEE_BPS = 5_000;

    // ─────────────────────────────────────────────────────────────────────────
    // Storage
    // ─────────────────────────────────────────────────────────────────────────

    /// @notice Address that receives all fees
    address public feeRecipient;

    /// @notice Management fee in basis points per year (e.g. 200 = 2%)
    uint256 public mgmtFeeBps;

    /// @notice Performance fee in basis points (e.g. 2000 = 20%)
    uint256 public perfFeeBps;

    /// @notice High-water-mark: highest total assets value seen, in base asset units.
    ///         Performance fees are only charged on gains above this level.
    uint256 public highWaterMark;

    /// @notice Timestamp of last management fee accrual
    uint256 public lastFeeTimestamp;

    /// @notice Approved DEX routers the agent can trade through
    mapping(address => bool) public approvedRouters;

    /// @notice Circuit breaker — agent can pause deposits/withdrawals
    bool public depositsPaused;

    // ─────────────────────────────────────────────────────────────────────────
    // Events
    // ─────────────────────────────────────────────────────────────────────────

    event ManagementFeeCharged(uint256 sharesMinted, uint256 assetsAUM);
    event PerformanceFeeCharged(uint256 sharesMinted, uint256 profitAboveHWM);
    event HighWaterMarkUpdated(uint256 oldHWM, uint256 newHWM);
    event FeeParamsUpdated(uint256 mgmtFeeBps, uint256 perfFeeBps, address feeRecipient);
    event RouterApproved(address indexed router, bool approved);
    event TradeExecuted(address indexed router, address indexed tokenIn, address indexed tokenOut, uint256 amountIn);
    event CircuitBreakerToggled(bool depositsPaused);

    // ─────────────────────────────────────────────────────────────────────────
    // Errors
    // ─────────────────────────────────────────────────────────────────────────

    error DepositsArePaused();
    error RouterNotApproved(address router);
    error TradeReverted();
    error FeeTooHigh(uint256 requested, uint256 max);
    error ZeroAddress();

    // ─────────────────────────────────────────────────────────────────────────
    // Constructor
    // ─────────────────────────────────────────────────────────────────────────

    /**
     * @param asset_           Base token depositors provide (e.g. USDC)
     * @param name_            Share token name (e.g. "Meridian-USDC-MM-Vault")
     * @param symbol_          Share token symbol (e.g. "mUSDC")
     * @param feeRecipient_    Address receiving fees (agent treasury)
     * @param mgmtFeeBps_      Annual management fee in bps (e.g. 200 = 2%)
     * @param perfFeeBps_      Performance fee in bps (e.g. 2000 = 20%)
     */
    constructor(
        IERC20 asset_,
        string memory name_,
        string memory symbol_,
        address feeRecipient_,
        uint256 mgmtFeeBps_,
        uint256 perfFeeBps_
    )
        ERC4626(asset_)
        ERC20(name_, symbol_)
        Ownable(msg.sender)
    {
        if (feeRecipient_ == address(0)) revert ZeroAddress();
        if (mgmtFeeBps_ > MAX_MGMT_FEE_BPS) revert FeeTooHigh(mgmtFeeBps_, MAX_MGMT_FEE_BPS);
        if (perfFeeBps_ > MAX_PERF_FEE_BPS) revert FeeTooHigh(perfFeeBps_, MAX_PERF_FEE_BPS);

        feeRecipient = feeRecipient_;
        mgmtFeeBps = mgmtFeeBps_;
        perfFeeBps = perfFeeBps_;

        highWaterMark = 0;
        lastFeeTimestamp = block.timestamp;
    }

    // ─────────────────────────────────────────────────────────────────────────
    // ERC-4626 overrides — fee accrual hooks
    // ─────────────────────────────────────────────────────────────────────────

    /// @inheritdoc ERC4626
    function totalAssets() public view override returns (uint256) {
        return IERC20(asset()).balanceOf(address(this));
    }

    /**
     * @notice Override deposit to accrue fees first, then update HWM.
     */
    function deposit(uint256 assets, address receiver)
        public
        override
        nonReentrant
        returns (uint256 shares)
    {
        if (depositsPaused) revert DepositsArePaused();
        _accrueManagementFee();
        shares = super.deposit(assets, receiver);
        _updateHighWaterMark();
    }

    /**
     * @notice Override mint (share-denominated deposit).
     */
    function mint(uint256 shares, address receiver)
        public
        override
        nonReentrant
        returns (uint256 assets)
    {
        if (depositsPaused) revert DepositsArePaused();
        _accrueManagementFee();
        assets = super.mint(shares, receiver);
        _updateHighWaterMark();
    }

    /**
     * @notice Override withdraw — accrue fees first so the exiting LP's share
     *         reflects up-to-date dilution.
     */
    function withdraw(uint256 assets, address receiver, address owner_)
        public
        override
        nonReentrant
        returns (uint256 shares)
    {
        _accrueManagementFee();
        _accruePerformanceFee();
        shares = super.withdraw(assets, receiver, owner_);
    }

    /**
     * @notice Override redeem (share-denominated withdrawal).
     */
    function redeem(uint256 shares, address receiver, address owner_)
        public
        override
        nonReentrant
        returns (uint256 assets)
    {
        _accrueManagementFee();
        _accruePerformanceFee();
        assets = super.redeem(shares, receiver, owner_);
    }

    // ─────────────────────────────────────────────────────────────────────────
    // Agent trade execution
    // ─────────────────────────────────────────────────────────────────────────

    /**
     * @notice Execute a trade through an approved DEX router.
     *         Only callable by the agent (owner). Uses vault's assets.
     * @param router      Approved DEX router address
     * @param tokenIn     Token being sold
     * @param tokenOut    Token being bought
     * @param amountIn    Amount of tokenIn to sell
     * @param minAmountOut Minimum output for slippage protection
     * @param data        Encoded swap calldata
     */
    function executeTrade(
        address router,
        address tokenIn,
        address tokenOut,
        uint256 amountIn,
        uint256 minAmountOut,
        bytes calldata data
    ) external onlyOwner nonReentrant returns (uint256 amountOut) {
        if (!approvedRouters[router]) revert RouterNotApproved(router);

        IERC20(tokenIn).safeIncreaseAllowance(router, amountIn);
        uint256 balanceBefore = IERC20(tokenOut).balanceOf(address(this));

        (bool success,) = router.call(data);
        if (!success) revert TradeReverted();

        amountOut = IERC20(tokenOut).balanceOf(address(this)) - balanceBefore;
        require(amountOut >= minAmountOut, "Slippage exceeded");

        emit TradeExecuted(router, tokenIn, tokenOut, amountIn);
    }

    /// @notice Approve or revoke a DEX router
    function setRouterApproval(address router, bool approved) external onlyOwner {
        approvedRouters[router] = approved;
        emit RouterApproved(router, approved);
    }

    // ─────────────────────────────────────────────────────────────────────────
    // Fee management
    // ─────────────────────────────────────────────────────────────────────────

    /**
     * @notice Update fee parameters. Accrues pending fees at old rates first.
     */
    function setFeeParams(
        uint256 newMgmtFeeBps,
        uint256 newPerfFeeBps,
        address newFeeRecipient
    ) external onlyOwner {
        if (newMgmtFeeBps > MAX_MGMT_FEE_BPS) revert FeeTooHigh(newMgmtFeeBps, MAX_MGMT_FEE_BPS);
        if (newPerfFeeBps > MAX_PERF_FEE_BPS) revert FeeTooHigh(newPerfFeeBps, MAX_PERF_FEE_BPS);
        if (newFeeRecipient == address(0)) revert ZeroAddress();

        // Accrue at old rates before changing
        _accrueManagementFee();
        _accruePerformanceFee();

        mgmtFeeBps = newMgmtFeeBps;
        perfFeeBps = newPerfFeeBps;
        feeRecipient = newFeeRecipient;

        emit FeeParamsUpdated(newMgmtFeeBps, newPerfFeeBps, newFeeRecipient);
    }

    /**
     * @notice Manually trigger fee accrual. Anyone can call; fees go to feeRecipient.
     */
    function harvestFees() external {
        _accrueManagementFee();
        _accruePerformanceFee();
    }

    // ─────────────────────────────────────────────────────────────────────────
    // Circuit breaker
    // ─────────────────────────────────────────────────────────────────────────

    function setDepositsPaused(bool paused) external onlyOwner {
        depositsPaused = paused;
        emit CircuitBreakerToggled(paused);
    }

    // ─────────────────────────────────────────────────────────────────────────
    // Internal — fee accounting
    // ─────────────────────────────────────────────────────────────────────────

    /**
     * @dev Accrue management fee by minting shares to feeRecipient.
     *      Uses time-weighted share minting to avoid NAV step-changes:
     *      shares_minted = totalSupply * mgmtFeeBps * elapsed / (SECONDS_PER_YEAR * BPS_DENOMINATOR)
     */
    function _accrueManagementFee() internal {
        uint256 elapsed = block.timestamp - lastFeeTimestamp;
        if (elapsed == 0 || mgmtFeeBps == 0 || totalSupply() == 0) {
            lastFeeTimestamp = block.timestamp;
            return;
        }

        // Dilute existing shares by minting fee shares proportionally
        // Fee shares = S * r * t / (1 - r*t)  ≈  S * r * t  for small r*t
        uint256 feeShares = (totalSupply() * mgmtFeeBps * elapsed)
            / (BPS_DENOMINATOR * SECONDS_PER_YEAR);

        if (feeShares > 0) {
            _mint(feeRecipient, feeShares);
            emit ManagementFeeCharged(feeShares, totalAssets());
        }

        lastFeeTimestamp = block.timestamp;
    }

    /**
     * @dev Accrue performance fee on AUM growth above the high-water-mark.
     *      Only called on withdrawal — depositors cannot be charged twice on same gains.
     */
    function _accruePerformanceFee() internal {
        if (perfFeeBps == 0 || totalSupply() == 0) return;

        uint256 aum = totalAssets();
        if (aum <= highWaterMark) return;

        uint256 profit = aum - highWaterMark;
        uint256 feeAssets = (profit * perfFeeBps) / BPS_DENOMINATOR;

        if (feeAssets == 0) return;

        // Convert fee assets to shares at current price (before HWM update)
        uint256 feeShares = convertToShares(feeAssets);
        if (feeShares > 0) {
            _mint(feeRecipient, feeShares);
            emit PerformanceFeeCharged(feeShares, profit);
        }

        // Update HWM to new peak (minus the fee taken)
        _updateHighWaterMark();
    }

    /**
     * @dev Update high-water-mark to current AUM if it's a new peak.
     */
    function _updateHighWaterMark() internal {
        uint256 aum = totalAssets();
        if (aum > highWaterMark) {
            emit HighWaterMarkUpdated(highWaterMark, aum);
            highWaterMark = aum;
        }
    }

    // ─────────────────────────────────────────────────────────────────────────
    // View helpers
    // ─────────────────────────────────────────────────────────────────────────

    /**
     * @notice Current share price in base asset units (1e18 precision)
     */
    function sharePrice() external view returns (uint256) {
        if (totalSupply() == 0) return 1e18;
        return (totalAssets() * 1e18) / totalSupply();
    }

    /**
     * @notice Pending management fee in shares (if harvested now)
     */
    function pendingManagementFee() external view returns (uint256 shares) {
        uint256 elapsed = block.timestamp - lastFeeTimestamp;
        if (elapsed == 0 || totalSupply() == 0) return 0;
        return (totalSupply() * mgmtFeeBps * elapsed)
            / (BPS_DENOMINATOR * SECONDS_PER_YEAR);
    }

    /**
     * @notice Pending performance fee in shares (if harvested now)
     */
    function pendingPerformanceFee() external view returns (uint256 shares) {
        uint256 aum = totalAssets();
        if (aum <= highWaterMark || totalSupply() == 0) return 0;
        uint256 profit = aum - highWaterMark;
        uint256 feeAssets = (profit * perfFeeBps) / BPS_DENOMINATOR;
        return convertToShares(feeAssets);
    }
}

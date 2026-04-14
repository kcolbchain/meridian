// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/token/ERC20/extensions/ERC4626.sol";
import "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import "@openzeppelin/contracts/access/Ownable.sol";
import "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import "@openzeppelin/contracts-upgradeable/proxy/utils/Initializable.sol";

/**
 * @title MeridianVaultRWA
 * @notice ERC-4626 vault with ERC-3643 compliance-gated share token.
 * Required for RWA (Real World Asset) strategies that must comply with
 * securities regulations — share transfers are gated by KYC/AML checks.
 * @dev Part of kcolbchain/meridian
 *
 * Compliance flow:
 * 1. Investor obtains KYC approval from trusted identity registry
 * 2. Vault operator adds investor to the compliance registry
 * 3. Investor can then deposit and receive compliance-gated share tokens
 * 4. Share transfers only allowed to/from whitelisted addresses
 */
contract MeridianVaultRWA is ERC4626, Ownable, ReentrancyGuard, Initializable {

    using SafeERC20 for IERC20;

    /// @notice ONCHAINID trusted identity registry for KYC compliance
    /// @dev ERC-3643: Transfer restrictions enforced via identity registry
    address public identityRegistry;

    /// @notice Compliance module that checks transfer restrictions
    address public complianceModule;

    /// @notice Flag: if true, only KYC'd addresses can deposit
    bool public kycRequired;

    /// @notice Whitelist of addresses that can interact with the vault
    mapping(address => bool) public whitelist;

    /// @notice Events for compliance
    event IdentityRegistryUpdated(address indexed registry);
    event ComplianceModuleUpdated(address indexed module);
    event WhitelistUpdated(address indexed account, bool status);
    event KycRequiredUpdated(bool status);

    error NotWhitelisted(address account);
    error KycRequired();
    error ComplianceFailure();

    modifier onlyWhitelisted() {
        if (kycRequired && !whitelist[msg.sender]) revert NotWhitelisted(msg.sender);
        _;
    }

    /// @param _asset Underlying RWA token (e.g., USDC)
    /// @param _identityRegistry ERC-3643 identity registry address
    /// @param _name Vault share token name
    /// @param _symbol Vault share token symbol
    constructor(
        IERC20Metadata _asset,
        address _identityRegistry,
        string memory _name,
        string memory _symbol
    ) ERC4626(_asset) Ownable(msg.sender) {
        identityRegistry = _identityRegistry;
        name_ = _name;
        symbol_ = _symbol;
    }

    /// @dev Initialize for upgradeable proxy pattern
    function initialize(
        IERC20Metadata _asset,
        address _identityRegistry,
        uint256 _maxSlippageBps,
        uint256 _performanceFee,
        uint256 _managementFee,
        bool _kycRequired
    ) external initializer {
        require(_performanceFee <= 5000);
        require(_managementFee <= 500);
        // Additional initialization...
        kycRequired = _kycRequired;
    }

    // ─── ERC-4626 Overrides (with KYC checks) ─────────────────────────────

    function deposit(uint256 assets, address receiver) external override onlyWhitelisted returns (uint256 shares) {
        require(assets > 0, "zero assets");
        shares = previewDeposit(assets);
        require(shares > 0, "zero shares");
        super.deposit(assets, receiver);
        emit Deposited(asset(), assets, shares);
    }

    function mint(uint256 shares, address receiver) external override onlyWhitelisted returns (uint256 assets) {
        require(shares > 0, "zero shares");
        assets = previewMint(shares);
        super.mint(shares, receiver);
        emit Deposited(asset(), assets, shares);
    }

    // ─── Share Transfer Overrides ─────────────────────────────────────────

    /// @dev Override to enforce compliance on share transfers (ERC-3643 pattern)
    function _beforeTokenTransfer(
        address from,
        address to,
        uint256 amount
    ) internal override {
        super._beforeTokenTransfer(from, to, amount);

        // Skip checks for mint/burn (deposit/withdraw already checked)
        if (from == address(0) || to == address(0)) return;

        // KYC whitelist check
        if (kycRequired) {
            require(whitelist[from], "sender not whitelisted");
            require(whitelist[to], "recipient not whitelisted");
        }

        // ERC-3643 compliance check via identity registry
        if (identityRegistry != address(0)) {
            _checkCompliance(from, to, amount);
        }
    }

    /// @dev ERC-3643 compliance check — delegate to compliance module
    function _checkCompliance(
        address from,
        address to,
        uint256 amount
    ) internal view {
        // In production, this calls identityRegistry.call VerifyTransfer(...)
        // Reverts if transfer violates KYC/AML requirements
        // Placeholder: in production, replace with real ERC-3643 compliance call
    }

    // ─── KYC / Compliance Admin ──────────────────────────────────────────

    /// @notice Set the ONCHAINID identity registry
    function setIdentityRegistry(address registry) external onlyOwner {
        identityRegistry = registry;
        emit IdentityRegistryUpdated(registry);
    }

    /// @notice Set the compliance module
    function setComplianceModule(address module) external onlyOwner {
        complianceModule = module;
        emit ComplianceModuleUpdated(module);
    }

    /// @notice Toggle KYC requirement
    function setKycRequired(bool required) external onlyOwner {
        kycRequired = required;
        emit KycRequiredUpdated(required);
    }

    /// @notice Add/remove address from whitelist
    function setWhitelist(address account, bool status) external onlyOwner {
        whitelist[account] = status;
        emit WhitelistUpdated(account, status);
    }

    /// @notice Bulk whitelist update
    function setWhitelistBatch(address[] calldata accounts, bool status) external onlyOwner {
        for (uint256 i = 0; i < accounts.length; i++) {
            whitelist[accounts[i]] = status;
            emit WhitelistUpdated(accounts[i], status);
        }
    }

    // ─── Trading (same as MeridianVault) ────────────────────────────────

    uint256 public maxSlippageBps;
    mapping(address => bool) public approvedRouters;
    bool public paused;

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

    function setMaxSlippage(uint256 newMaxBps) external onlyOwner {
        maxSlippageBps = newMaxBps;
        emit SlippageUpdated(newMaxBps);
    }

    function approveRouter(address router, bool approved) external onlyOwner {
        approvedRouters[router] = approved;
        emit RouterApproved(router, approved);
    }

    function setCircuitBreaker(bool _paused) external onlyOwner {
        paused = _paused;
        emit CircuitBreakerTriggered(_paused);
    }

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

    function topUp(uint256 amount) external onlyOwner {
        IERC20(asset()).safeTransferFrom(msg.sender, address(this), amount);
    }

    function maxDeposit(address) external view override returns (uint256) {
        return IERC20(asset()).balanceOf(address(this));
    }

    function maxRedeem(address owner) external view override returns (uint256) {
        return balanceOf(owner);
    }
}

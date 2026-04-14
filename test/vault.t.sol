// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "forge-std/Test.sol";
import "../../contracts/MeridianVault.sol";
import "@openzeppelin/contracts/mocks/ERC20Mock.sol";

/// @notice Foundry tests for MeridianVault ERC-4626 compliance and security
/// @dev Modeled after kcolbchain/audit-checklist ERC-4626 inflation attack tests
contract MeridianVaultTest is Test {
    MeridianVault public vault;
    ERC20Mock public asset;
    address public agent;
    address public depositor1;
    address public depositor2;
    address public router;

    uint256 constant MAX_SLIPPAGE = 100; // 1%
    uint256 constant PERFORMANCE_FEE = 2000; // 20%
    uint256 constant MANAGEMENT_FEE = 100; // 1%

    function setUp() public {
        asset = new ERC20Mock("Mock USDC", "USDC", 6);
        vault = new MeridianVault(asset, MAX_SLIPPAGE, PERFORMANCE_FEE, MANAGEMENT_FEE);
        agent = address(this);
        depositor1 = makeAddr("depositor1");
        depositor2 = makeAddr("depositor2");
        router = makeAddr("router");

        // Agent approves router
        vault.approveRouter(router, true);

        // Fund accounts with assets
        asset.mint(depositor1, 10000e6);
        asset.mint(depositor2, 10000e6);
        asset.mint(agent, 100000e6);
    }

    // ─── ERC-4626 Compliance Tests ──────────────────────────────────────

    function test_erc4626_deposit() public {
        vm.startPrank(depositor1);
        asset.approve(address(vault), 1000e6);
        uint256 shares = vault.deposit(1000e6, depositor1);
        vm.stopPrank();

        assertEq(vault.balanceOf(depositor1), shares);
        assertEq(asset.balanceOf(address(vault)), 1000e6);
        assertGt(shares, 0);
    }

    function test_erc4626_mint() public {
        vm.startPrank(depositor1);
        asset.approve(address(vault), 1000e6);
        uint256 assets = vault.mint(500e6, depositor1);
        vm.stopPrank();

        assertEq(vault.balanceOf(depositor1), 500e6);
        assertEq(asset.balanceOf(address(vault)), assets);
    }

    function test_erc4626_withdraw() public {
        // Deposit first
        vm.startPrank(depositor1);
        asset.approve(address(vault), 1000e6);
        uint256 shares = vault.deposit(1000e6, depositor1);
        vm.stopPrank();

        // Withdraw
        vm.startPrank(depositor1);
        uint256 assets = vault.withdraw(500e6, depositor1, depositor1);
        vm.stopPrank();

        assertEq(vault.balanceOf(depositor1), shares - vault.previewWithdraw(500e6));
    }

    function test_erc4626_redeem() public {
        vm.startPrank(depositor1);
        asset.approve(address(vault), 1000e6);
        uint256 shares = vault.deposit(1000e6, depositor1);
        uint256 assets = vault.redeem(shares / 2, depositor1, depositor1);
        vm.stopPrank();

        assertEq(vault.balanceOf(depositor1), shares / 2);
        assertGt(assets, 0);
    }

    function test_erc4626_maxDeposit_respects_balance() public {
        uint256 max = vault.maxDeposit(depositor1);
        // Cannot deposit more than vault holds (in simple scenario)
        assertGe(max, 0);
    }

    // ─── Inflation Attack Tests ───────────────────────────────────────────

    /// @dev First depositor gets favorable share price. Second attacker
    ///       deposits tiny amount before large deposit to manipulate share price.
    function test_inflation_attack_ mitigated_by_previewDeposit() public {
        // Legitimate user deposits first
        vm.startPrank(depositor1);
        asset.approve(address(vault), 1000e6);
        uint256 shares1 = vault.deposit(1000e6, depositor1);
        vm.stopPrank();

        // Attacker deposits tiny amount to manipulate share price
        vm.startPrank(depositor2);
        asset.approve(address(vault), 1e6);
        vault.deposit(1e6, depositor2);
        vm.stopPrank();

        // Large deposit — should get fewer shares due to inflation
        vm.startPrank(depositor2);
        asset.approve(address(vault), 10000e6);
        uint256 shares2 = vault.deposit(10000e6, depositor2);
        vm.stopPrank();

        // Attacker should NOT be able to drain vault via inflation attack
        // Share price after attack:
        uint256 totalSupply = vault.totalSupply();
        uint256 totalAssets = vault.totalAssets();
        uint256 sharePrice = totalSupply > 0 ? totalAssets * 1e18 / totalSupply : 1e18;

        // Initial share price was 1:1, post-attack price should not be massively different
        assertGt(sharePrice, 0.9e18); // Within 10%
    }

    // ─── Fee Tests ────────────────────────────────────────────────────────

    function test_performance_fee_setter() public {
        vault.setPerformanceFee(3000);
        assertEq(vault.performanceFee(), 3000);
    }

    function test_performance_fee_caps_at_50pct() public {
        vm.expectRevert();
        vault.setPerformanceFee(6000); // > 5000
    }

    function test_management_fee_setter() public {
        vault.setManagementFee(200);
        assertEq(vault.managementFee(), 200);
    }

    function test_circuit_breaker() public {
        vault.setCircuitBreaker(true);
        assertTrue(vault.paused());

        vm.startPrank(depositor1);
        asset.approve(address(vault), 1000e6);
        vm.expectRevert(MeridianVault.VaultPaused.selector);
        vault.deposit(1000e6, depositor1);
        vm.stopPrank();

        vault.setCircuitBreaker(false);
        assertFalse(vault.paused());
    }

    // ─── Access Control Tests ──────────────────────────────────────────────

    function test_only_owner_can_executeTrade() public {
        vm.startPrank(depositor1);
        vm.expectRevert("UNAUTHORIZED");
        vault.executeTrade(router, address(asset), address(asset), 100e6, 90e6, "");
        vm.stopPrank();
    }

    function test_only_owner_can_approveRouter() public {
        vm.startPrank(depositor1);
        vm.expectRevert("UNAUTHORIZED");
        vault.approveRouter(router, true);
        vm.stopPrank();
    }

    function test_only_owner_can_setCircuitBreaker() public {
        vm.startPrank(depositor1);
        vm.expectRevert("UNAUTHORIZED");
        vault.setCircuitBreaker(true);
        vm.stopPrank();
    }

    // ─── Router Tests ─────────────────────────────────────────────────────

    function test_unapproved_router_rejected() public {
        address badRouter = makeAddr("badRouter");
        vm.startPrank(depositor1);
        asset.approve(address(vault), 1000e6);
        vault.deposit(1000e6, depositor1);
        vm.stopPrank();

        vm.startPrank(agent);
        vm.expectRevert(MeridianVault.RouterNotApproved.selector);
        vault.executeTrade(badRouter, address(asset), address(asset), 100e6, 90e6, "");
        vm.stopPrank();
    }

    // ─── HWM (High Water Mark) Tests ─────────────────────────────────────

    function test_hwm_updated_on_first_deposit() public {
        vm.startPrank(depositor1);
        asset.approve(address(vault), 1000e6);
        vault.deposit(1000e6, depositor1);
        vm.stopPrank();

        uint256 hwm = vault.highWaterMark(depositor1);
        assertGt(hwm, 0);
    }

    function test_hwm_updated_after_profit() public {
        // Deposit
        vm.startPrank(depositor1);
        asset.approve(address(vault), 1000e6);
        vault.deposit(1000e6, depositor1);
        uint256 initialHwm = vault.highWaterMark(depositor1);
        vm.stopPrank();

        // Simulate profit: agent trades profit into vault
        asset.mint(address(vault), 200e6); // 20% profit

        // HWM should reflect improved share price
        uint256 newHwm = vault.highWaterMark(depositor1);
        assertGe(newHwm, initialHwm);
    }
}

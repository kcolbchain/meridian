const { expect } = require("chai");
const { ethers } = require("hardhat");
const { time, loadFixture } = require("@nomicfoundation/hardhat-network-helpers");

// ───────────────────────────────────────────────────────────────────────────
// MeridianVaultERC4626 — Solidity test-suite
//
// Property focus areas (as required by the task):
//   1. Share-price monotonicity (no free value extraction on round trips)
//   2. Performance fee charged only above the high-water-mark
//   3. Fee caps respected (<=10% mgmt / <=50% perf)
//   4. deposit / mint / withdraw / redeem round-trips
//   5. Lightweight property/fuzz-style loops over randomised inputs
//
// The vault's totalAssets() is simply the base-asset balance it holds, so we
// simulate realised trading profit by minting the base asset directly into the
// vault (the same path the contract's fee math is built to handle).
// ───────────────────────────────────────────────────────────────────────────

const BPS = 10_000n;
const SECONDS_PER_YEAR = 365n * 24n * 60n * 60n;
const MAX_MGMT_FEE_BPS = 1_000n; // 10%
const MAX_PERF_FEE_BPS = 5_000n; // 50%

describe("MeridianVaultERC4626", function () {
  // Default deployment: 6-decimal USDC-like asset, 2% mgmt / 20% perf.
  async function deployFixture() {
    const [owner, alice, bob, feeRecipient, router] = await ethers.getSigners();

    const Mock = await ethers.getContractFactory("MockERC20");
    const asset = await Mock.deploy("USD Coin", "USDC", 6);
    await asset.waitForDeployment();

    const Vault = await ethers.getContractFactory("MeridianVaultERC4626");
    const vault = await Vault.deploy(
      await asset.getAddress(),
      "Meridian USDC MM Vault",
      "mUSDC",
      feeRecipient.address,
      200n, // 2% mgmt
      2000n // 20% perf
    );
    await vault.waitForDeployment();

    const unit = 10n ** 6n; // 1 USDC
    // Fund LPs and approve the vault.
    for (const lp of [alice, bob]) {
      await asset.mint(lp.address, 1_000_000n * unit);
      await asset
        .connect(lp)
        .approve(await vault.getAddress(), ethers.MaxUint256);
    }

    return { owner, alice, bob, feeRecipient, router, asset, vault, unit };
  }

  // Zero-fee deployment so round-trip / monotonicity tests are not perturbed
  // by fee dilution; isolates pure ERC4626 accounting + rounding direction.
  async function deployNoFeeFixture() {
    const [owner, alice, bob, feeRecipient] = await ethers.getSigners();

    const Mock = await ethers.getContractFactory("MockERC20");
    const asset = await Mock.deploy("USD Coin", "USDC", 6);
    await asset.waitForDeployment();

    const Vault = await ethers.getContractFactory("MeridianVaultERC4626");
    const vault = await Vault.deploy(
      await asset.getAddress(),
      "Meridian USDC MM Vault",
      "mUSDC",
      feeRecipient.address,
      0n,
      0n
    );
    await vault.waitForDeployment();

    const unit = 10n ** 6n;
    for (const lp of [alice, bob]) {
      await asset.mint(lp.address, 1_000_000n * unit);
      await asset
        .connect(lp)
        .approve(await vault.getAddress(), ethers.MaxUint256);
    }

    return { owner, alice, bob, feeRecipient, asset, vault, unit };
  }

  // Performance-fee-only deployment: 0% mgmt, 20% perf. Isolates the HWM /
  // performance-fee logic from time-based management-fee drift (hardhat
  // advances block.timestamp ~1s per tx, which would otherwise accrue a tiny
  // management fee between transactions).
  async function deployPerfOnlyFixture() {
    const [owner, alice, bob, feeRecipient] = await ethers.getSigners();

    const Mock = await ethers.getContractFactory("MockERC20");
    const asset = await Mock.deploy("USD Coin", "USDC", 6);
    await asset.waitForDeployment();

    const Vault = await ethers.getContractFactory("MeridianVaultERC4626");
    const vault = await Vault.deploy(
      await asset.getAddress(),
      "Meridian USDC MM Vault",
      "mUSDC",
      feeRecipient.address,
      0n, // no management fee
      2000n // 20% performance fee
    );
    await vault.waitForDeployment();

    const unit = 10n ** 6n;
    for (const lp of [alice, bob]) {
      await asset.mint(lp.address, 1_000_000n * unit);
      await asset
        .connect(lp)
        .approve(await vault.getAddress(), ethers.MaxUint256);
    }

    return { owner, alice, bob, feeRecipient, asset, vault, unit };
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Construction & configuration invariants
  // ─────────────────────────────────────────────────────────────────────────
  describe("construction & fee caps", function () {
    it("stores constructor params and exposes the fee caps", async function () {
      const { vault, feeRecipient } = await loadFixture(deployFixture);
      expect(await vault.mgmtFeeBps()).to.equal(200n);
      expect(await vault.perfFeeBps()).to.equal(2000n);
      expect(await vault.feeRecipient()).to.equal(feeRecipient.address);
      expect(await vault.MAX_MGMT_FEE_BPS()).to.equal(MAX_MGMT_FEE_BPS);
      expect(await vault.MAX_PERF_FEE_BPS()).to.equal(MAX_PERF_FEE_BPS);
    });

    it("reverts when constructed with a management fee above the 10% cap", async function () {
      const [, , , feeRecipient] = await ethers.getSigners();
      const Mock = await ethers.getContractFactory("MockERC20");
      const asset = await Mock.deploy("USD Coin", "USDC", 6);
      const Vault = await ethers.getContractFactory("MeridianVaultERC4626");
      await expect(
        Vault.deploy(
          await asset.getAddress(),
          "v",
          "v",
          feeRecipient.address,
          MAX_MGMT_FEE_BPS + 1n,
          2000n
        )
      )
        .to.be.revertedWithCustomError(Vault, "FeeTooHigh")
        .withArgs(MAX_MGMT_FEE_BPS + 1n, MAX_MGMT_FEE_BPS);
    });

    it("reverts when constructed with a performance fee above the 50% cap", async function () {
      const [, , , feeRecipient] = await ethers.getSigners();
      const Mock = await ethers.getContractFactory("MockERC20");
      const asset = await Mock.deploy("USD Coin", "USDC", 6);
      const Vault = await ethers.getContractFactory("MeridianVaultERC4626");
      await expect(
        Vault.deploy(
          await asset.getAddress(),
          "v",
          "v",
          feeRecipient.address,
          200n,
          MAX_PERF_FEE_BPS + 1n
        )
      )
        .to.be.revertedWithCustomError(Vault, "FeeTooHigh")
        .withArgs(MAX_PERF_FEE_BPS + 1n, MAX_PERF_FEE_BPS);
    });

    it("reverts when constructed with a zero fee recipient", async function () {
      const Mock = await ethers.getContractFactory("MockERC20");
      const asset = await Mock.deploy("USD Coin", "USDC", 6);
      const Vault = await ethers.getContractFactory("MeridianVaultERC4626");
      await expect(
        Vault.deploy(
          await asset.getAddress(),
          "v",
          "v",
          ethers.ZeroAddress,
          200n,
          2000n
        )
      ).to.be.revertedWithCustomError(Vault, "ZeroAddress");
    });

    it("setFeeParams enforces both caps and rejects the zero recipient", async function () {
      const { vault, feeRecipient } = await loadFixture(deployFixture);
      await expect(
        vault.setFeeParams(MAX_MGMT_FEE_BPS + 1n, 2000n, feeRecipient.address)
      ).to.be.revertedWithCustomError(vault, "FeeTooHigh");
      await expect(
        vault.setFeeParams(200n, MAX_PERF_FEE_BPS + 1n, feeRecipient.address)
      ).to.be.revertedWithCustomError(vault, "FeeTooHigh");
      await expect(
        vault.setFeeParams(200n, 2000n, ethers.ZeroAddress)
      ).to.be.revertedWithCustomError(vault, "ZeroAddress");

      // The boundary values are accepted.
      await expect(
        vault.setFeeParams(MAX_MGMT_FEE_BPS, MAX_PERF_FEE_BPS, feeRecipient.address)
      ).to.not.be.reverted;
      expect(await vault.mgmtFeeBps()).to.equal(MAX_MGMT_FEE_BPS);
      expect(await vault.perfFeeBps()).to.equal(MAX_PERF_FEE_BPS);
    });

    it("only the owner may change fee params, routers, or the breaker", async function () {
      const { vault, alice, feeRecipient } = await loadFixture(deployFixture);
      await expect(
        vault.connect(alice).setFeeParams(100n, 1000n, feeRecipient.address)
      ).to.be.revertedWithCustomError(vault, "OwnableUnauthorizedAccount");
      await expect(
        vault.connect(alice).setRouterApproval(alice.address, true)
      ).to.be.revertedWithCustomError(vault, "OwnableUnauthorizedAccount");
      await expect(
        vault.connect(alice).setDepositsPaused(true)
      ).to.be.revertedWithCustomError(vault, "OwnableUnauthorizedAccount");
    });
  });

  // ─────────────────────────────────────────────────────────────────────────
  // deposit / mint / withdraw / redeem round-trips
  // ─────────────────────────────────────────────────────────────────────────
  describe("round-trips (deposit/mint/withdraw/redeem)", function () {
    it("deposit then redeem returns no more than was deposited (zero fees, no yield)", async function () {
      const { vault, asset, alice, unit } = await loadFixture(deployNoFeeFixture);
      const amount = 50_000n * unit;
      const before = await asset.balanceOf(alice.address);

      const shares = await vault.connect(alice).deposit.staticCall(amount, alice.address);
      await vault.connect(alice).deposit(amount, alice.address);
      expect(await vault.balanceOf(alice.address)).to.equal(shares);

      await vault.connect(alice).redeem(shares, alice.address, alice.address);
      const after = await asset.balanceOf(alice.address);

      // A solo LP with no yield and no fees can never extract more than deposited.
      expect(after).to.be.lte(before);
      // Rounding loss is at most a couple of base units (ERC4626 virtual offset).
      expect(before - after).to.be.lte(2n);
    });

    it("mint then withdraw round-trips without minting free value", async function () {
      const { vault, asset, alice, unit } = await loadFixture(deployNoFeeFixture);
      const sharesWanted = 25_000n * (10n ** 6n);
      const before = await asset.balanceOf(alice.address);

      const assetsPaid = await vault.connect(alice).mint.staticCall(sharesWanted, alice.address);
      await vault.connect(alice).mint(sharesWanted, alice.address);
      expect(await vault.balanceOf(alice.address)).to.equal(sharesWanted);

      const maxAssets = await vault.maxWithdraw(alice.address);
      await vault.connect(alice).withdraw(maxAssets, alice.address, alice.address);

      const after = await asset.balanceOf(alice.address);
      expect(after).to.be.lte(before);
      // What you can withdraw never exceeds what you paid to mint.
      expect(maxAssets).to.be.lte(assetsPaid);
    });

    it("previewDeposit/previewRedeem are consistent with actual execution", async function () {
      const { vault, alice, unit } = await loadFixture(deployNoFeeFixture);
      const amount = 12_345n * unit;

      const previewShares = await vault.previewDeposit(amount);
      const actualShares = await vault.connect(alice).deposit.staticCall(amount, alice.address);
      expect(actualShares).to.equal(previewShares);

      await vault.connect(alice).deposit(amount, alice.address);
      const bal = await vault.balanceOf(alice.address);
      const previewAssets = await vault.previewRedeem(bal);
      const actualAssets = await vault
        .connect(alice)
        .redeem.staticCall(bal, alice.address, alice.address);
      expect(actualAssets).to.equal(previewAssets);
    });

    it("a withdrawing LP cannot drain another LP's principal (no free value extraction)", async function () {
      const { vault, asset, alice, bob, unit } = await loadFixture(deployNoFeeFixture);
      await vault.connect(alice).deposit(100_000n * unit, alice.address);
      await vault.connect(bob).deposit(100_000n * unit, bob.address);

      const bobBefore = await asset.balanceOf(bob.address);
      const aliceShares = await vault.balanceOf(alice.address);
      await vault.connect(alice).redeem(aliceShares, alice.address, alice.address);

      // Bob's claim must still be (approximately) his full principal afterwards.
      const bobClaim = await vault.maxWithdraw(bob.address);
      expect(bobClaim).to.be.gte(100_000n * unit - 2n);

      await vault.connect(bob).redeem(await vault.balanceOf(bob.address), bob.address, bob.address);
      const bobAfter = await asset.balanceOf(bob.address);
      expect(bobAfter - bobBefore).to.be.gte(100_000n * unit - 2n);
    });
  });

  // ─────────────────────────────────────────────────────────────────────────
  // Share-price monotonicity / no free value extraction
  // ─────────────────────────────────────────────────────────────────────────
  describe("share-price monotonicity", function () {
    it("share price never decreases due to a deposit (deposits don't dilute price)", async function () {
      const { vault, alice, bob, unit } = await loadFixture(deployNoFeeFixture);
      await vault.connect(alice).deposit(40_000n * unit, alice.address);

      const p0 = await vault.sharePrice();
      await vault.connect(bob).deposit(73_219n * unit, bob.address);
      const p1 = await vault.sharePrice();

      // Adding fairly-priced capital must not reduce per-share value.
      expect(p1).to.be.gte(p0 - 1n); // tolerate 1-unit floor rounding
    });

    it("realised yield (asset airdrop) raises share price strictly", async function () {
      const { vault, asset, alice, unit } = await loadFixture(deployNoFeeFixture);
      await vault.connect(alice).deposit(50_000n * unit, alice.address);

      const p0 = await vault.sharePrice();
      // Simulate trading profit landing back in the base asset.
      await asset.mint(await vault.getAddress(), 10_000n * unit);
      const p1 = await vault.sharePrice();
      expect(p1).to.be.gt(p0);
    });

    it("a deposit/redeem cycle by an attacker cannot increase the honest LP's redeemable assets below their stake", async function () {
      const { vault, asset, alice, bob, unit } = await loadFixture(deployNoFeeFixture);
      await vault.connect(alice).deposit(100_000n * unit, alice.address);
      const aliceClaimBefore = await vault.maxWithdraw(alice.address);

      // Attacker churns in and out repeatedly.
      for (let i = 0; i < 5; i++) {
        const s = await vault.connect(bob).deposit.staticCall(7_000n * unit, bob.address);
        await vault.connect(bob).deposit(7_000n * unit, bob.address);
        await vault.connect(bob).redeem(s, bob.address, bob.address);
      }

      const aliceClaimAfter = await vault.maxWithdraw(alice.address);
      // Alice's claim must not have shrunk (beyond trivial rounding) — no value leaked to the attacker.
      expect(aliceClaimAfter).to.be.gte(aliceClaimBefore - 5n);
    });
  });

  // ─────────────────────────────────────────────────────────────────────────
  // Management fee
  // ─────────────────────────────────────────────────────────────────────────
  describe("management fee", function () {
    it("accrues ~mgmtFeeBps of supply per year as fee shares", async function () {
      const { vault, feeRecipient, alice, unit } = await loadFixture(deployFixture);
      await vault.connect(alice).deposit(100_000n * unit, alice.address);

      const supplyBefore = await vault.totalSupply();
      await time.increase(Number(SECONDS_PER_YEAR));
      await vault.harvestFees();

      const feeShares = await vault.balanceOf(feeRecipient.address);
      // ~2% of supply over one year. harvestFees() mines a block, so the realised
      // elapsed time is one year plus a couple of seconds of block drift; the fee
      // is therefore >= the exact one-year amount by a sliver.
      const expectedOneYear = (supplyBefore * 200n) / BPS; // 2% of supply
      const driftPerSecond = (supplyBefore * 200n) / (BPS * SECONDS_PER_YEAR);
      expect(feeShares).to.be.gte(expectedOneYear);
      expect(feeShares).to.be.lte(expectedOneYear + driftPerSecond * 10n + 1n);
    });

    it("charges zero management fee when mgmtFeeBps is 0", async function () {
      const { vault, feeRecipient, alice, unit } = await loadFixture(deployNoFeeFixture);
      await vault.connect(alice).deposit(100_000n * unit, alice.address);
      await time.increase(Number(SECONDS_PER_YEAR));
      await vault.harvestFees();
      expect(await vault.balanceOf(feeRecipient.address)).to.equal(0n);
    });

    it("pendingManagementFee matches the shares actually minted on harvest", async function () {
      const { vault, feeRecipient, alice, unit } = await loadFixture(deployFixture);
      await vault.connect(alice).deposit(100_000n * unit, alice.address);
      await time.increase(180 * 24 * 60 * 60); // half a year-ish

      const pending = await vault.pendingManagementFee();
      await vault.harvestFees();
      // harvest advances time by ~1 block; pending was computed one block earlier,
      // so the realised amount is >= pending and within a tiny block-drift margin.
      const realised = await vault.balanceOf(feeRecipient.address);
      expect(realised).to.be.gte(pending);
    });

    it("management fee dilutes LP share value but is bounded by the fee rate", async function () {
      const { vault, alice, unit } = await loadFixture(deployFixture);
      await vault.connect(alice).deposit(100_000n * unit, alice.address);
      const claimBefore = await vault.maxWithdraw(alice.address);

      await time.increase(Number(SECONDS_PER_YEAR));
      await vault.harvestFees();

      const claimAfter = await vault.maxWithdraw(alice.address);
      // LP gives up some value to fees, but never more than the gross fee rate.
      expect(claimAfter).to.be.lt(claimBefore);
      const lost = claimBefore - claimAfter;
      // Loss bounded by ~2% of AUM (the annual mgmt fee).
      expect(lost).to.be.lte((claimBefore * 200n) / BPS + 2n);
    });
  });

  // ─────────────────────────────────────────────────────────────────────────
  // Performance fee — only above the high-water-mark
  // ─────────────────────────────────────────────────────────────────────────
  describe("performance fee & high-water-mark", function () {
    it("charges no performance fee when there is no profit above HWM", async function () {
      const { vault, feeRecipient, alice, unit } = await loadFixture(deployPerfOnlyFixture);
      await vault.connect(alice).deposit(100_000n * unit, alice.address);
      // HWM is now ~100k. With no yield and a 0% management fee, harvesting mints nothing.
      const before = await vault.balanceOf(feeRecipient.address);
      expect(await vault.pendingPerformanceFee()).to.equal(0n);
      await vault.harvestFees();
      const after = await vault.balanceOf(feeRecipient.address);
      expect(after).to.equal(before); // exactly zero fee shares with no profit + no mgmt fee
    });

    it("charges performance fee only on the gain above the high-water-mark", async function () {
      const { vault, asset, feeRecipient, alice, unit } = await loadFixture(deployPerfOnlyFixture);
      await vault.connect(alice).deposit(100_000n * unit, alice.address);
      const hwm0 = await vault.highWaterMark();

      // Simulate +20k profit landing back in the base asset.
      await asset.mint(await vault.getAddress(), 20_000n * unit);
      const profit = 20_000n * unit;
      const grossFeeAssets = (profit * 2000n) / BPS; // 20% of 20k = 4k (gross target)

      const feeRecipientSharesBefore = await vault.balanceOf(feeRecipient.address);
      const pendingPerf = await vault.pendingPerformanceFee();
      expect(pendingPerf).to.be.gt(0n);

      await vault.harvestFees();
      const minted = (await vault.balanceOf(feeRecipient.address)) - feeRecipientSharesBefore;
      expect(minted).to.equal(pendingPerf); // harvest mints exactly the pending amount

      // The fee is realised as freshly minted shares, so its redeemable value is the
      // gross 4k diluted by its own minting: strictly below 4k but a large fraction of it.
      const feeValue = await vault.previewRedeem(minted);
      expect(feeValue).to.be.lt(grossFeeAssets); // dilution makes it strictly less than gross
      expect(feeValue).to.be.gt((grossFeeAssets * 90n) / 100n); // still >90% of the 4k target

      // HWM advanced to the new peak (totalAssets is unchanged by share minting).
      const hwm1 = await vault.highWaterMark();
      expect(hwm1).to.be.gt(hwm0);
      expect(hwm1).to.equal(120_000n * unit);

      // The remaining LP value plus the fee value reconciles to total assets — no value created or destroyed.
      const aliceValue = await vault.previewRedeem(await vault.balanceOf(alice.address));
      expect(aliceValue + feeValue).to.be.lte(await vault.totalAssets());
      expect(aliceValue + feeValue).to.be.gte((await vault.totalAssets()) - 3n);
    });

    it("does NOT double-charge after a drawdown-and-recovery (HWM protection)", async function () {
      const { vault, asset, feeRecipient, alice, unit } = await loadFixture(deployPerfOnlyFixture);
      await vault.connect(alice).deposit(100_000n * unit, alice.address);

      // Gain +20k → harvest perf fee, HWM → 120k.
      await asset.mint(await vault.getAddress(), 20_000n * unit);
      await vault.harvestFees();
      const feeSharesAfterFirst = await vault.balanceOf(feeRecipient.address);
      const hwmPeak = await vault.highWaterMark();
      expect(hwmPeak).to.equal(120_000n * unit);

      // Drawdown: burn 30k out of the vault (AUM 120k → 90k, below HWM).
      await asset.burn(await vault.getAddress(), 30_000n * unit);
      expect(await vault.pendingPerformanceFee()).to.equal(0n);
      await vault.harvestFees();
      // No new perf-fee shares while under water (0% mgmt fee → exact equality).
      expect(await vault.balanceOf(feeRecipient.address)).to.equal(feeSharesAfterFirst);

      // Recover back up to 115k — still below the 120k HWM.
      await asset.mint(await vault.getAddress(), 25_000n * unit);
      expect(await vault.pendingPerformanceFee()).to.equal(0n);
      await vault.harvestFees();
      expect(await vault.balanceOf(feeRecipient.address)).to.equal(feeSharesAfterFirst);

      // Cross above the HWM to 125k → only the 5k above 120k is charged.
      await asset.mint(await vault.getAddress(), 10_000n * unit);
      const newProfit = (await vault.totalAssets()) - hwmPeak; // = 5k
      expect(newProfit).to.equal(5_000n * unit);
      const pending = await vault.pendingPerformanceFee();
      expect(pending).to.be.gt(0n);
      await vault.harvestFees();
      const newFeeShares = (await vault.balanceOf(feeRecipient.address)) - feeSharesAfterFirst;
      const newFeeValue = await vault.previewRedeem(newFeeShares);
      const grossExpected = (5_000n * unit * 2000n) / BPS; // 20% of 5k = 1k gross target
      // Realised fee value is the gross diluted by its own minting: <1k but >90% of it.
      expect(newFeeValue).to.be.lt(grossExpected);
      expect(newFeeValue).to.be.gt((grossExpected * 90n) / 100n);
    });

    it("charges zero performance fee when perfFeeBps is 0 even on large gains", async function () {
      const { vault, asset, feeRecipient, alice, unit } = await loadFixture(deployNoFeeFixture);
      await vault.connect(alice).deposit(100_000n * unit, alice.address);
      await asset.mint(await vault.getAddress(), 500_000n * unit);
      await vault.harvestFees();
      expect(await vault.balanceOf(feeRecipient.address)).to.equal(0n);
      expect(await vault.pendingPerformanceFee()).to.equal(0n);
    });

    it("HighWaterMarkUpdated event fires on a profitable harvest", async function () {
      const { vault, asset, alice, unit } = await loadFixture(deployFixture);
      await vault.connect(alice).deposit(100_000n * unit, alice.address);
      await asset.mint(await vault.getAddress(), 15_000n * unit);
      await expect(vault.harvestFees()).to.emit(vault, "HighWaterMarkUpdated");
    });
  });

  // ─────────────────────────────────────────────────────────────────────────
  // Circuit breaker & access control on deposits
  // ─────────────────────────────────────────────────────────────────────────
  describe("circuit breaker", function () {
    it("blocks deposit and mint while paused, allows them once resumed", async function () {
      const { vault, alice, unit } = await loadFixture(deployFixture);
      await vault.setDepositsPaused(true);
      await expect(
        vault.connect(alice).deposit(1_000n * unit, alice.address)
      ).to.be.revertedWithCustomError(vault, "DepositsArePaused");
      await expect(
        vault.connect(alice).mint(1_000n * (10n ** 6n), alice.address)
      ).to.be.revertedWithCustomError(vault, "DepositsArePaused");

      await vault.setDepositsPaused(false);
      await expect(vault.connect(alice).deposit(1_000n * unit, alice.address)).to.not.be.reverted;
    });

    it("still allows withdrawals while deposits are paused", async function () {
      const { vault, alice, unit } = await loadFixture(deployFixture);
      await vault.connect(alice).deposit(10_000n * unit, alice.address);
      await vault.setDepositsPaused(true);
      const shares = await vault.balanceOf(alice.address);
      await expect(
        vault.connect(alice).redeem(shares, alice.address, alice.address)
      ).to.not.be.reverted;
    });
  });

  // ─────────────────────────────────────────────────────────────────────────
  // Agent trade execution access control
  // ─────────────────────────────────────────────────────────────────────────
  describe("executeTrade access control", function () {
    it("reverts for a non-owner caller", async function () {
      const { vault, alice, asset } = await loadFixture(deployFixture);
      await expect(
        vault
          .connect(alice)
          .executeTrade(alice.address, await asset.getAddress(), await asset.getAddress(), 1n, 0n, "0x")
      ).to.be.revertedWithCustomError(vault, "OwnableUnauthorizedAccount");
    });

    it("reverts when the router is not approved", async function () {
      const { vault, owner, asset, router } = await loadFixture(deployFixture);
      await expect(
        vault
          .connect(owner)
          .executeTrade(router.address, await asset.getAddress(), await asset.getAddress(), 1n, 0n, "0x")
      )
        .to.be.revertedWithCustomError(vault, "RouterNotApproved")
        .withArgs(router.address);
    });
  });

  // ─────────────────────────────────────────────────────────────────────────
  // Property / fuzz-style loops (deterministic PRNG for reproducibility)
  // ─────────────────────────────────────────────────────────────────────────
  describe("property: randomised round-trips never extract free value", function () {
    // xorshift32 — small deterministic PRNG so failures are reproducible.
    function makeRng(seed) {
      let s = seed >>> 0;
      return function next() {
        s ^= s << 13; s >>>= 0;
        s ^= s >> 17;
        s ^= s << 5; s >>>= 0;
        return s >>> 0;
      };
    }

    it("over 40 random deposit→redeem cycles, an LP never withdraws more than deposited (no yield, no fees)", async function () {
      const { vault, asset, alice, unit } = await loadFixture(deployNoFeeFixture);
      const rng = makeRng(0xC0FFEE);

      for (let i = 0; i < 40; i++) {
        // amount between 1 and 100_000 USDC
        const amt = (BigInt(rng() % 100_000) + 1n) * unit;
        const before = await asset.balanceOf(alice.address);
        const shares = await vault.connect(alice).deposit.staticCall(amt, alice.address);
        await vault.connect(alice).deposit(amt, alice.address);
        await vault.connect(alice).redeem(shares, alice.address, alice.address);
        const after = await asset.balanceOf(alice.address);
        // Never profit from a pure round trip; rounding only ever costs the LP.
        expect(after).to.be.lte(before);
      }
    });

    it("over random profit events the share price is non-decreasing (zero fees)", async function () {
      const { vault, asset, alice, unit } = await loadFixture(deployNoFeeFixture);
      const rng = makeRng(0xBEEF);
      await vault.connect(alice).deposit(100_000n * unit, alice.address);

      let prev = await vault.sharePrice();
      for (let i = 0; i < 30; i++) {
        const gain = BigInt(rng() % 50_000) * unit; // may be 0
        if (gain > 0n) await asset.mint(await vault.getAddress(), gain);
        const cur = await vault.sharePrice();
        expect(cur).to.be.gte(prev);
        prev = cur;
      }
    });

    it("over random gains, harvested performance fee never exceeds the perfFeeBps share of profit above HWM", async function () {
      const { vault, asset, feeRecipient, alice, unit } = await loadFixture(deployFixture);
      const rng = makeRng(0x1234);
      await vault.connect(alice).deposit(100_000n * unit, alice.address);

      for (let i = 0; i < 20; i++) {
        const hwmBefore = await vault.highWaterMark();
        const gain = (BigInt(rng() % 30_000) + 1n) * unit;
        await asset.mint(await vault.getAddress(), gain);

        const aum = await vault.totalAssets();
        const profitAboveHwm = aum > hwmBefore ? aum - hwmBefore : 0n;

        const feeSharesBefore = await vault.balanceOf(feeRecipient.address);
        await vault.harvestFees();
        const feeSharesMinted = (await vault.balanceOf(feeRecipient.address)) - feeSharesBefore;
        const feeValue = await vault.previewRedeem(feeSharesMinted);

        // Performance component never exceeds 20% of the profit above the prior HWM
        // (plus a small mgmt-fee allowance and rounding band).
        const cap = (profitAboveHwm * 2000n) / BPS + (aum * 200n) / BPS + 2n;
        expect(feeValue).to.be.lte(cap);
      }
    });
  });
});

const { ethers } = require("hardhat");

async function main() {
  const [deployer] = await ethers.getSigners();
  console.log("Deploying Meridian contracts with:", deployer.address);
  console.log("Balance:", ethers.formatEther(await ethers.provider.getBalance(deployer.address)), "ETH\n");

  // 1. Deploy OracleAdapter
  const OracleAdapter = await ethers.getContractFactory("OracleAdapter");
  const oracle = await OracleAdapter.deploy();
  await oracle.waitForDeployment();
  const oracleAddr = await oracle.getAddress();
  console.log("OracleAdapter:", oracleAddr);

  // 2. Deploy MeridianVault (100 bps = 1% max slippage)
  const MeridianVault = await ethers.getContractFactory("MeridianVault");
  const vault = await MeridianVault.deploy(100);
  await vault.waitForDeployment();
  const vaultAddr = await vault.getAddress();
  console.log("MeridianVault:", vaultAddr);

  // 3. Deploy StrategyExecutor
  const StrategyExecutor = await ethers.getContractFactory("StrategyExecutor");
  const executor = await StrategyExecutor.deploy(
    vaultAddr,
    oracleAddr,
    200,   // 2% base spread
    ethers.parseEther("10"),  // max 10 ETH position size
    ethers.parseEther("5")    // rebalance at 5 ETH imbalance
  );
  await executor.waitForDeployment();
  const executorAddr = await executor.getAddress();
  console.log("StrategyExecutor:", executorAddr);

  console.log("\n--- Deployment Complete ---");
  console.log(JSON.stringify({
    oracle: oracleAddr,
    vault: vaultAddr,
    executor: executorAddr,
    deployer: deployer.address,
    network: (await ethers.provider.getNetwork()).name,
    chainId: Number((await ethers.provider.getNetwork()).chainId),
  }, null, 2));
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});

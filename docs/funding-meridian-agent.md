# Funding a Meridian Agent

> How to deposit assets into a MeridianVault and earn a share of the agent's market-making profits.

## Overview

The `MeridianVault` is an [ERC-4626](https://eips.ethereum.org/EIPS/eip-4626) compliant vault. Depositors supply assets (e.g., USDC) and receive vault shares proportional to their contribution. The agent uses these assets to provide liquidity on DEXes and splits the resulting profits with depositors.

## For Crypto-Native Agents (ERC-4626 — `MeridianVault`)

### Deposit

```python
from web3 import Web3

w3 = Web3(Web3.HTTPProvider("https://base-mainnet.public.blastapi.io"))

vault_address = "0x..."  # deployed vault address
vault_abi = [...]          # ERC-4626 ABI

vault = w3.eth.contract(address=vault_address, abi=vault_abi)
asset = w3.eth.contract(address=vault.functions.asset().call(), abi=ERC20_ABI)

# Approve vault to spend USDC
asset.functions.approve(vault_address, amount).transact({"from": depositor})

# Deposit USDC and receive share tokens
shares = vault.functions.deposit(amount, depositor).transact({"from": depositor})
```

### Withdraw

```python
# Redeem shares for underlying assets
assets = vault.functions.redeem(shares, depositor, depositor).transact({"from": depositor})
```

### Monitor Performance

```python
# Check your share balance
shares = vault.functions.balanceOf(depositor).call()

# Check vault total assets (AUM)
aum = vault.functions.totalAssets().call()

# Current share price (assets per share)
share_price = aum / vault.functions.totalSupply().call()
```

## For RWA Strategies (ERC-4626 + ERC-3643 — `MeridianVaultRWA`)

RWA agents require KYC compliance. Before depositing, your address must be whitelisted by the vault operator.

### Check KYC Status

```python
vault_rwa = w3.eth.contract(address=vault_address, abi=VAULT_RWA_ABI)
is_whitelisted = vault_rwa.functions.whitelist(depositor).call()
kyc_required = vault_rwa.functions.kycRequired().call()

if kyc_required and not is_whitelisted:
    print("KYC approval required — contact vault operator")
```

### KYC Process

1. Complete identity verification via the trusted identity registry (ERC-3643)
2. Vault operator adds your address to the whitelist
3. You can then deposit and receive compliance-gated share tokens

### Transfer Restrictions

RWA share tokens **cannot** be transferred to non-whitelisted addresses. This ensures compliance with securities regulations.

## Fees

| Fee | Description | Default |
|-----|-------------|---------|
| Management Fee | Annual fee charged on AUM | 1% (100 bps) |
| Performance Fee | Share of agent profits | 20% (2000 bps) |

The **high water mark (HWM)** mechanism prevents depositors from being double-charged during volatility periods. Profits are calculated as gains above the previous peak share price.

## Agent Profit Flow

```
DEX Trade Profit (e.g., 5% on $10,000 USDC position = $500)
        │
        ├── 80% (agentProfitShare) → Agent (via performance fee claim)
        │
        └── 20% → Depositors (via vault share price appreciation / HWM)
```

## Smart Contract Addresses

| Contract | Network | Notes |
|----------|---------|-------|
| `MeridianVault` | Base | For crypto-native market-making agents |
| `MeridianVaultRWA` | Base | For KYC-gated RWA strategies |

## Security Considerations

- **Smart contract risk**: MeridianVault is unaudited. DYOR before depositing.
- **Agent risk**: The agent controls all trading. If the agent's strategy fails, depositor assets may decrease in value.
- **Circuit breaker**: The agent can pause the vault via `setCircuitBreaker(true)` if the strategy detects anomalies.
- **Inflation attack**: Mitigated by using `previewDeposit()` — always check the share price before depositing.

## Contract Addresses (Testnet)

```
MeridianVault: 0x... (Base Sepolia)
MeridianVaultRWA: 0x... (Base Sepolia)
```

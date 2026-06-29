// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "@openzeppelin/contracts/token/ERC20/ERC20.sol";

/**
 * @title MockERC20
 * @notice Minimal mintable ERC20 with configurable decimals, used only by the
 *         Solidity test-suite for MeridianVaultERC4626. Not deployed to mainnet.
 * @dev Mirrors a USDC-style asset when constructed with 6 decimals, or a generic
 *      18-decimal token otherwise. `mint` is unrestricted on purpose so tests can
 *      simulate trading profit by crediting the vault directly.
 */
contract MockERC20 is ERC20 {
    uint8 private immutable _customDecimals;

    constructor(string memory name_, string memory symbol_, uint8 decimals_)
        ERC20(name_, symbol_)
    {
        _customDecimals = decimals_;
    }

    function decimals() public view override returns (uint8) {
        return _customDecimals;
    }

    function mint(address to, uint256 amount) external {
        _mint(to, amount);
    }

    function burn(address from, uint256 amount) external {
        _burn(from, amount);
    }
}

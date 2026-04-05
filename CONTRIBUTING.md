# Contributing to meridian

Thanks for your interest! meridian is a [kcolbchain](https://kcolbchain.com) open-source project.

## Quick Start

```bash
git clone https://github.com/YOUR_USERNAME/meridian.git
cd meridian
pip install -r requirements.txt
python -m pytest tests/
python -m src.agents.rwa_market_maker --simulate
```

### Contracts
```bash
cd deploy
npm install
npx hardhat compile
npx hardhat test
```

## Finding Work

- [Open issues](https://github.com/kcolbchain/meridian/issues)
- `good-first-issue` / `help-wanted` tags

## Code Style

- Python: black formatter, type hints preferred
- Solidity: follow existing patterns, OpenZeppelin v5
- Tests required for new features

## Community

[kcolbchain.com](https://kcolbchain.com) | [Join](https://kcolbchain.com/join.html)

## License
MIT

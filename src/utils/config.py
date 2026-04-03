"""Configuration management."""

import yaml
from pathlib import Path


def load_config(path: str = "config/default.yaml") -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def merge_configs(base: dict, override: dict) -> dict:
    """Deep merge override into base."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = merge_configs(result[key], value)
        else:
            result[key] = value
    return result

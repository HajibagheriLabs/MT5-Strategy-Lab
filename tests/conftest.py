from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if (REPO_ROOT / "local.toml").exists():
        return
    skip = pytest.mark.skip(reason="needs local.toml pointing at a MetaTrader 5 terminal")
    for item in items:
        if "mt5" in item.keywords:
            item.add_marker(skip)

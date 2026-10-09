import pytest

from strategylab.config import local_config_path


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if local_config_path().exists():
        return
    skip = pytest.mark.skip(reason="needs local.toml pointing at a MetaTrader 5 terminal")
    for item in items:
        if "mt5" in item.keywords:
            item.add_marker(skip)

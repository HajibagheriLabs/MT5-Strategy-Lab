"""StrategyLab never trades: only mt5_data.py may touch the real package, and only to read."""

import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "backend" / "strategylab"
MT5_DATA = PACKAGE / "mt5_data.py"
SOURCES = sorted([*PACKAGE.rglob("*.py"), *(ROOT / "scripts").glob("*.py"), ROOT / "tasks.py"])

# Calls and constants that place, change or close orders and positions.
TRADING = re.compile(
    r"\b(order_send|order_check|order_calc_margin|order_calc_profit|positions_get|orders_get"
    r"|TRADE_ACTION_\w+|ORDER_TYPE_\w+|ORDER_FILLING_\w+|Buy|Sell|PositionClose)\b"
)
IMPORTS_REAL_PACKAGE = re.compile(
    r"^\s*(import\s+MetaTrader5\b|from\s+MetaTrader5\s+import\b)"
    r"|import_module\(\s*['\"]MetaTrader5['\"]"
    r"|__import__\(\s*['\"]MetaTrader5['\"]",
    re.M,
)


def code_only(text: str) -> str:
    """Source with comments and docstrings blanked, so prose about trading is not flagged."""
    text = re.sub(r'"""[\s\S]*?"""', "", text)
    return "\n".join(line.split("#", 1)[0] for line in text.splitlines())


def test_mt5_data_only_reads():
    found = sorted(set(TRADING.findall(code_only(MT5_DATA.read_text(encoding="utf-8")))))
    assert found == []


def test_the_scan_would_catch_a_trading_call():
    sample = "import MetaTrader5 as mt5\nmt5.order_send({'action': mt5.TRADE_ACTION_DEAL})\n"
    assert set(TRADING.findall(code_only(sample))) == {"order_send", "TRADE_ACTION_DEAL"}


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: str(p.relative_to(ROOT)))
def test_only_mt5_data_imports_the_real_package(path):
    text = path.read_text(encoding="utf-8")
    if path == MT5_DATA:
        assert IMPORTS_REAL_PACKAGE.search(text)
    else:
        assert not IMPORTS_REAL_PACKAGE.search(text)


def test_the_import_scan_would_catch_other_forms():
    for line in (
        "import MetaTrader5 as mt5",
        "from MetaTrader5 import order_send",
        "importlib.import_module('MetaTrader5')",
        '__import__("MetaTrader5")',
    ):
        assert IMPORTS_REAL_PACKAGE.search(line), line


def test_the_simulator_never_loads_the_real_package():
    probe = (
        "import sys; import strategylab.sim.runner, strategylab.sim.shim, strategylab.metrics; "
        "print('MetaTrader5' in sys.modules)"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", probe], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "False"

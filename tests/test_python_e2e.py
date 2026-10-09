from datetime import date
from pathlib import Path

import pytest

from strategylab.backtest import run_python_strategy
from strategylab.config import load_settings
from strategylab.result import Engine
from strategylab.sim.runner import SimOutcome

pytestmark = pytest.mark.mt5
SAMPLE = Path(__file__).resolve().parents[1] / "samples" / "python" / "ma_cross.py"


def test_the_sample_strategy_runs_unmodified_and_trades():
    settings = load_settings()
    symbol = next(
        (
            name
            for server in settings.terminal.history_servers()
            for name in sorted(
                p.name
                for p in (settings.terminal.data_dir / "bases" / server / "history").iterdir()
            )
            if name.upper().startswith("EURUSD")
        ),
        None,
    )
    if symbol is None:
        pytest.skip("no EURUSD history in the terminal yet")
    run = run_python_strategy(
        SAMPLE,
        settings,
        symbol=symbol,
        timeframe="H1",
        date_from=date(2025, 3, 1),
        date_to=date(2025, 5, 1),
        timeout_s=900,
    )
    assert run.outcome is SimOutcome.SUCCESS, run.message
    assert run.stats["status"] == "finished"
    assert run.result.meta.engine is Engine.PYTHON_SIM
    assert run.result.trade_count > 5
    assert all(d.time is not None for d in run.result.deals)

from datetime import date

import pytest

from strategylab.backtest import run_mql5_backtest
from strategylab.config import load_settings
from strategylab.report import parse_number
from strategylab.tester import Outcome

pytestmark = pytest.mark.mt5


@pytest.fixture(scope="module")
def settings():
    return load_settings()


@pytest.fixture(scope="module")
def fx_symbol(settings):
    """An EURUSD-like symbol the terminal already holds history for, whatever the broker suffix."""
    for server in settings.terminal.history_servers():
        history = settings.terminal.data_dir / "bases" / server / "history"
        names = sorted(p.name for p in history.iterdir() if p.name.upper().startswith("EURUSD"))
        if names:
            return min(names, key=len)
    pytest.skip("no EURUSD history in the terminal yet")


def moving_average(settings):
    return settings.terminal.experts_dir / "Examples" / "Moving Average" / "Moving Average.mq5"


def test_moving_average_month(settings, fx_symbol):
    run = run_mql5_backtest(
        moving_average(settings),
        settings,
        symbol=fx_symbol,
        timeframe="H1",
        date_from=date(2025, 3, 1),
        date_to=date(2025, 4, 1),
        timeout_s=600,
    )
    assert run.tester.outcome is Outcome.SUCCESS, run.tester.message
    result = run.result
    assert result is not None
    assert result.meta.symbol == fx_symbol
    assert result.trade_count == int(result.reported["Total Trades"])
    net = sum(d.profit + d.commission + d.swap for d in result.deals if d.type != "balance")
    assert net == pytest.approx(parse_number(result.reported["Total Net Profit"]), abs=0.005)
    assert result.meta.notes == []
    assert all(d.time is not None for d in result.deals)
    assert run.result_path.is_file()
    assert {"terminal", "tester", "agent"} <= set(run.tester.log_paths)


def test_unknown_symbol_produces_no_report(settings):
    run = run_mql5_backtest(
        moving_average(settings),
        settings,
        symbol="NO_SUCH_SYMBOL",
        timeframe="H1",
        date_from=date(2025, 3, 3),
        date_to=date(2025, 3, 4),
        timeout_s=300,
    )
    assert run.tester.outcome is Outcome.NO_REPORT
    assert "NO_SUCH_SYMBOL" in run.tester.message


def test_overridden_input_reaches_the_tester(settings, fx_symbol):
    run = run_mql5_backtest(
        moving_average(settings),
        settings,
        symbol=fx_symbol,
        timeframe="H1",
        date_from=date(2025, 3, 3),
        date_to=date(2025, 3, 15),
        parameters={"MovingPeriod": "24"},
        timeout_s=300,
    )
    assert run.tester.outcome in (Outcome.SUCCESS, Outcome.ZERO_TRADES)
    inputs = run.result.meta.parameters
    assert inputs["MovingPeriod"] == "24"
    assert inputs["MovingShift"] == "6"
    assert run.result.metrics["trades"] == run.result.trade_count

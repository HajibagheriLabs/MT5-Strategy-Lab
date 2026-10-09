import json
import textwrap
from datetime import UTC, date, datetime

import pytest
from simdata import bars, write_dataset

from strategylab.result import Engine, TickModel
from strategylab.server_clock import ServerClock, WeekOffset
from strategylab.sim.runner import PythonRunSpec, SimOutcome, run_python_backtest

CLOCK = ServerClock(
    "Test-Server", "EURUSD@", (WeekOffset(datetime(2025, 1, 6), 2),), datetime.now(UTC)
)
POLLER = """
import json
import sys
import time
from datetime import date, datetime

import MetaTrader5 as mt5

mt5.initialize()
seen = {"registered": sys.modules["MetaTrader5"] is mt5, "file": mt5.__file__}
observations = []
bought = False
try:
    while True:
        tick = mt5.symbol_info_tick("EURUSD")
        if tick is not None:
            now = datetime.now()
            observations.append(
                [time.time(), tick.time, now.isoformat(), isinstance(now, datetime),
                 date.today().isoformat()]
            )
            if not bought:
                result = mt5.order_send({
                    "action": mt5.TRADE_ACTION_DEAL, "symbol": "EURUSD", "type": mt5.ORDER_TYPE_BUY,
                    "volume": 0.1, "type_filling": mt5.ORDER_FILLING_FOK,
                })
                bought = result.retcode == mt5.TRADE_RETCODE_DONE
        before = time.time()
        time.sleep(30)
        seen["slept_at_least"] = time.time() - before >= 30
finally:
    seen["observations"] = observations[:3]
    seen["count"] = len(observations)
    with open("observations.json", "w") as handle:
        json.dump(seen, handle)
"""


@pytest.fixture(scope="module")
def dataset(tmp_path_factory):
    folder = tmp_path_factory.mktemp("data")
    rows = [
        (1.1000 + i * 1e-5, 1.1002 + i * 1e-5, 1.0998 + i * 1e-5, 1.1001 + i * 1e-5)
        for i in range(240)
    ]
    return write_dataset(folder, bars(datetime(2025, 1, 6, 10, 0), rows))


def run(tmp_path, dataset, source, *, timeout_s=120, name="strategy.py"):
    script = tmp_path / name
    script.write_text(textwrap.dedent(source), encoding="utf-8")
    m1, spec_file = dataset
    spec = PythonRunSpec(
        script=script,
        symbol="EURUSD@",
        timeframe="H1",
        date_from=date(2025, 1, 6),
        date_to=date(2025, 1, 7),
    )
    return run_python_backtest(
        spec,
        m1=m1,
        spec_file=spec_file,
        run_dir=tmp_path / "run",
        run_id="test",
        server="Test-Server",
        clock=CLOCK,
        timeout_s=timeout_s,
    )


def test_an_endless_polling_loop_runs_through_history_and_ends(tmp_path, dataset):
    sim = run(tmp_path, dataset, POLLER)
    assert sim.outcome is SimOutcome.SUCCESS, sim.message
    assert sim.stats["status"] == "finished"
    seen = json.loads((tmp_path / "run" / "observations.json").read_text())
    assert seen["registered"] is True
    assert "strategylab" in seen["file"]
    assert seen["slept_at_least"] is True
    clock_now, tick_time, now_text, is_datetime, today = seen["observations"][0]
    assert tick_time <= clock_now < tick_time + 60
    assert now_text.startswith("2025-01-06T10:0")
    assert is_datetime is True
    assert today == "2025-01-06"
    result = sim.result
    assert result.meta.engine is Engine.PYTHON_SIM
    assert result.meta.model is TickModel.OHLC_M1
    assert "not the Strategy Tester" in result.meta.fidelity
    assert result.trade_count == 1
    assert result.deals[-1].comment == "end of test"
    assert result.deals[1].time == datetime(2025, 1, 6, 8, 0, tzinfo=UTC)
    assert result.meta.server_utc_offsets_h == [2]
    assert result.metrics["trades"] == 1
    assert sim.result_path.is_file()


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("raise ValueError('boom')\n", "boom"),
        (
            "import MetaTrader5 as mt5\nwhile True:\n    mt5.symbol_info_tick('EURUSD')\n",
            "without sleeping",
        ),
        (
            "import MetaTrader5 as mt5\nmt5.market_book_add('EURUSD')\n",
            "MetaTrader5.market_book_add",
        ),
        (
            "import MetaTrader5 as mt5\n"
            "mt5.copy_rates_from_pos('GBPUSD', mt5.TIMEFRAME_H1, 0, 5)\n",
            "only EURUSD@ is available",
        ),
        ("import sys\nsys.exit(3)\n", "exited with 3"),
    ],
)
def test_failures_are_reported_with_the_reason(tmp_path, dataset, source, message):
    sim = run(tmp_path, dataset, source)
    assert sim.outcome is SimOutcome.ERROR
    assert message in sim.message


def test_a_strategy_that_stops_by_itself(tmp_path, dataset):
    source = "import time\nfor _ in range(3):\n    time.sleep(60)\n"
    sim = run(tmp_path, dataset, source)
    assert sim.outcome is SimOutcome.ZERO_TRADES
    assert any("stopped by itself" in note for note in sim.result.meta.notes)


def test_a_stuck_strategy_is_stopped_at_the_timeout(tmp_path, dataset):
    sim = run(tmp_path, dataset, "while True:\n    pass\n", timeout_s=5)
    assert sim.outcome is SimOutcome.TIMEOUT
    assert "within 5 s" in sim.message


def test_print_output_is_kept(tmp_path, dataset):
    sim = run(tmp_path, dataset, "print('hello from the strategy')\n")
    assert "hello from the strategy" in sim.log_path.read_text(encoding="utf-8")

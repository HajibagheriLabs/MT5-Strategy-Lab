import json
import textwrap
import threading
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


def run(
    tmp_path, dataset, source, *, timeout_s=120, name="strategy.py", args=(), constants=None, **kw
):
    script = tmp_path / name
    script.write_text(textwrap.dedent(source), encoding="utf-8")
    m1, spec_file = dataset
    spec = PythonRunSpec(
        script=script,
        symbol="EURUSD@",
        timeframe="H1",
        date_from=date(2025, 1, 6),
        date_to=date(2025, 1, 7),
        args=tuple(args),
        constants=constants or {},
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
        **kw,
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


def test_the_script_gets_its_arguments(tmp_path, dataset):
    sim = run(tmp_path, dataset, "import sys\nprint('ARGS', sys.argv[1:])\n", args=["--fast", "7"])
    assert "ARGS ['--fast', '7']" in sim.log_path.read_text(encoding="utf-8")


def test_print_output_is_kept(tmp_path, dataset):
    sim = run(tmp_path, dataset, "print('hello from the strategy')\n")
    assert "hello from the strategy" in sim.log_path.read_text(encoding="utf-8")


def test_any_text_can_be_printed(tmp_path, dataset):
    sim = run(tmp_path, dataset, "print('profit \u20ac, \u0633\u0648\u062f')\n")
    assert sim.outcome is SimOutcome.ZERO_TRADES, sim.message
    assert "profit \u20ac, \u0633\u0648\u062f" in sim.log_path.read_text(encoding="utf-8")


def test_printed_lines_are_followed_and_parsing_announced(tmp_path, dataset):
    lines, parsing = [], []
    source = "print('first')\nprint('second', end='')\n"
    sim = run(
        tmp_path,
        dataset,
        source,
        on_log=lines.append,
        on_parsing=lambda: parsing.append(True),
    )
    assert sim.outcome is SimOutcome.ZERO_TRADES
    assert lines[:2] == ["first", "second"]
    assert parsing == [True]


def test_cancel_stops_the_strategy(tmp_path, dataset):
    cancel = threading.Event()
    threading.Timer(1.0, cancel.set).start()
    sim = run(
        tmp_path, dataset, "print('spinning', flush=True)\nwhile True:\n    pass\n", cancel=cancel
    )
    assert sim.outcome is SimOutcome.CANCELLED
    assert sim.message == "Cancelled; the strategy was stopped."
    assert sim.elapsed_s < 30


def test_constants_are_replaced_without_touching_the_file(tmp_path, dataset):
    source = "FAST = 3  # fast period\nNAME = 'a'\nprint('values', FAST, NAME, __name__)\n"
    sim = run(tmp_path, dataset, source, constants={"FAST": 7, "NAME": "b"})
    log = sim.log_path.read_text(encoding="utf-8")
    assert "values 7 b __main__" in log
    assert (tmp_path / "strategy.py").read_text(encoding="utf-8").startswith("FAST = 3")


def test_an_error_in_a_script_with_replaced_constants_points_at_its_line(tmp_path, dataset):
    source = "FAST = 3\n\nraise RuntimeError(FAST)\n"
    sim = run(tmp_path, dataset, source, constants={"FAST": 9})
    assert sim.outcome is SimOutcome.ERROR
    assert 'strategy.py", line 3' in sim.message
    assert "RuntimeError: 9" in sim.message

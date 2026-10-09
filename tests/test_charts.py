import json
import threading
from datetime import UTC, date, datetime

import pandas as pd
import pytest

from strategylab.charts import (
    BarStore,
    BarsUnavailableError,
    aggregate,
    source_timeframe,
    window,
)
from strategylab.config import Settings, TerminalInstall
from strategylab.result import Engine
from strategylab.store import RunRecord, RunSettings, RunStatus


def epoch(*parts):
    return int(datetime(*parts, tzinfo=UTC).timestamp())


def minutes(start, count, price=1.1):
    rows = []
    for i in range(count):
        p = price + i * 0.0001
        rows.append((start + 60 * i, p, p + 0.0003, p - 0.0002, p + 0.0001))
    return pd.DataFrame(rows, columns=["time", "open", "high", "low", "close"])


@pytest.mark.parametrize(
    ("timeframe", "source"),
    [("M1", "M1"), ("M2", "M1"), ("M10", "M5"), ("M20", "M5"), ("H2", "H1"), ("H6", "H1"),
     ("H8", "H4"), ("H12", "H4"), ("D1", "D1"), ("W1", "D1"), ("MN1", "D1")],
)  # fmt: skip
def test_source_timeframe(timeframe, source):
    assert source_timeframe(timeframe) == source


def test_aggregate_builds_hours_from_minutes():
    m1 = minutes(epoch(2025, 1, 6, 9, 30), 120)
    h1 = aggregate(m1, "H1")
    assert list(h1["time"]) == [epoch(2025, 1, 6, 9), epoch(2025, 1, 6, 10), epoch(2025, 1, 6, 11)]
    first = m1[m1["time"] < epoch(2025, 1, 6, 10)]
    assert h1.iloc[0]["open"] == first.iloc[0]["open"]
    assert h1.iloc[0]["close"] == first.iloc[-1]["close"]
    assert h1.iloc[0]["high"] == first["high"].max()
    assert h1.iloc[0]["low"] == first["low"].min()


def test_weeks_open_on_sunday():
    days = pd.DataFrame(
        [(epoch(2025, 1, d), 1.0, 1.1, 0.9, 1.05) for d in (5, 6, 7, 12, 13)],
        columns=["time", "open", "high", "low", "close"],
    )
    assert list(aggregate(days, "W1")["time"]) == [epoch(2025, 1, 5), epoch(2025, 1, 12)]


def test_windows():
    frame = minutes(epoch(2025, 1, 6), 100)
    part, first = window(frame, count=10)
    assert (first, len(part)) == (90, 10)
    part, first = window(frame, around=epoch(2025, 1, 6, 0, 50), count=10)
    assert first == 45 and part.iloc[5]["time"] == epoch(2025, 1, 6, 0, 50)
    part, first = window(frame, before=epoch(2025, 1, 6, 0, 5), count=10)
    assert (first, len(part)) == (0, 5)
    part, first = window(frame, after=epoch(2025, 1, 6, 1, 35), count=10)
    assert (first, len(part)) == (96, 4)
    part, first = window(frame, around=epoch(2025, 1, 6, 1, 39), count=10)
    assert (first, len(part)) == (90, 10)


def run_record(run_id="r1", timeframe="H1"):
    return RunRecord(
        id=run_id,
        strategy_hash="h",
        strategy_name="s",
        engine=Engine.MT5_TESTER,
        status=RunStatus.DONE,
        settings=RunSettings(
            symbol="EURUSD@",
            timeframe=timeframe,
            date_from=date(2025, 1, 6),
            date_to=date(2025, 1, 7),
        ),
        created_at=datetime.now(UTC),
    )


@pytest.fixture
def settings(tmp_path):
    install = TerminalInstall(tmp_path / "mt5", tmp_path / "mt5", True)
    return Settings(install, "test", "Demo", tmp_path / "workspace")


def test_a_python_run_uses_the_bars_it_was_simulated_on(tmp_path, settings):
    m1 = tmp_path / "M1.parquet"
    minutes(epoch(2025, 1, 5, 23), 180).to_parquet(m1)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "sim_job.json").write_text(json.dumps({"m1": str(m1)}), encoding="utf-8")

    def never(*args):
        raise AssertionError("must not use the terminal")

    store = BarStore(lambda: settings, threading.Lock(), never)
    bars = store.bars(run_record(), run_dir)
    assert list(bars["time"]) == [epoch(2025, 1, 6, 0), epoch(2025, 1, 6, 1)]


def test_a_tester_run_exports_once_and_waits_for_a_free_terminal(tmp_path, settings):
    calls = []

    def exporter(settings_, symbol, timeframe, start, end):
        calls.append((symbol, timeframe, start, end))
        return aggregate(minutes(epoch(2025, 1, 6), 300), timeframe)

    lock = threading.Lock()
    store = BarStore(lambda: settings, lock, exporter)
    lock.acquire()
    with pytest.raises(BarsUnavailableError, match="busy with a run"):
        store.bars(run_record(timeframe="M5"), tmp_path / "none")
    lock.release()
    bars = store.bars(run_record(timeframe="M5"), tmp_path / "none")
    assert len(bars) == 60
    again = BarStore(lambda: settings, lock, exporter)
    assert len(again.bars(run_record("r2", timeframe="M5"), tmp_path / "none")) == 60
    assert calls == [("EURUSD@", "M5", date(2025, 1, 6), date(2025, 1, 7))]

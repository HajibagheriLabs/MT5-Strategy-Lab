from datetime import UTC, date, datetime, timedelta

import pytest

from strategylab.result import Engine
from strategylab.store import (
    NotFoundError,
    Parameter,
    RunRecord,
    RunSettings,
    RunStatus,
    Store,
    StrategyRecord,
)

T0 = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)


def strategy(hash_="abc123", **changes):
    record = StrategyRecord(
        hash=hash_,
        name="Moving Average.mq5",
        kind="mq5",
        engine=Engine.MT5_TESTER,
        folder=r"C:\lab\MQL5\Experts\StrategyLab\abc123",
        entry="Moving Average.mq5",
        parameters=[Parameter(name="Lots", kind="real", default="0.1", source="input")],
        parameter_source="source",
        ok=True,
        uploaded_at=T0,
    )
    return record.model_copy(update=changes)


def run(run_id="r1", minutes=0, **changes):
    record = RunRecord(
        id=run_id,
        strategy_hash="abc123",
        strategy_name="Moving Average",
        engine=Engine.MT5_TESTER,
        status=RunStatus.QUEUED,
        settings=RunSettings(
            symbol="EURUSD@", timeframe="H1", date_from=date(2025, 1, 1), date_to=date(2025, 2, 1)
        ),
        created_at=T0 + timedelta(minutes=minutes),
    )
    return record.model_copy(update=changes)


@pytest.fixture
def store(tmp_path):
    db = Store(tmp_path / "lab.sqlite")
    db.save_strategy(strategy())
    return db


def test_strategies_round_trip(store):
    saved = store.strategy("abc123")
    assert saved == strategy()
    assert [s.hash for s in store.strategies()] == ["abc123"]
    with pytest.raises(NotFoundError):
        store.strategy("missing")


def test_uploading_the_same_strategy_again_keeps_its_name_and_date(store):
    later = strategy(name="renamed.mq5", uploaded_at=T0 + timedelta(days=1), ok=False)
    saved = store.save_strategy(later)
    assert (saved.name, saved.uploaded_at, saved.ok) == ("Moving Average.mq5", T0, False)


def test_runs_round_trip_with_artefacts(store):
    store.add_run(run(artefacts={"result": r"C:\w\runs\r1\result.json"}))
    loaded = store.run("r1")
    assert loaded.settings.symbol == "EURUSD@"
    assert loaded.status is RunStatus.QUEUED
    assert loaded.artefacts == {"result": r"C:\w\runs\r1\result.json"}


def test_update_run(store):
    store.add_run(run())
    updated = store.update_run(
        "r1",
        status=RunStatus.DONE,
        finished_at=T0,
        metrics={"net_profit": 989.59, "profit_factor": None},
        stage_seconds={"running": 12.5},
        trade_count=267,
    )
    assert updated.status is RunStatus.DONE
    assert updated.metrics == {"net_profit": 989.59, "profit_factor": None}
    assert updated.stage_seconds == {"running": 12.5}
    assert updated.finished_at == T0
    with pytest.raises(ValueError, match="Not run fields"):
        store.update_run("r1", colour="red")
    with pytest.raises(NotFoundError):
        store.update_run("missing", status=RunStatus.DONE)


def test_listing_filters_and_orders_newest_first(store):
    store.save_strategy(strategy("py1", kind="py", engine=Engine.PYTHON_SIM))
    store.add_run(run("a", 0))
    store.add_run(run("b", 1, status=RunStatus.DONE))
    store.add_run(run("c", 2, strategy_hash="py1", engine=Engine.PYTHON_SIM))
    store.set_artefacts("b", {"report": "report.htm"})
    assert [r.id for r in store.runs()] == ["c", "b", "a"]
    assert [r.id for r in store.runs(status="done")] == ["b"]
    assert [r.id for r in store.runs(engine="python_sim")] == ["c"]
    assert [r.id for r in store.runs(strategy_hash="abc123")] == ["b", "a"]
    assert [r.id for r in store.runs(symbol="GBPUSD@")] == []
    assert [r.id for r in store.runs(limit=1, offset=1)] == ["b"]
    assert store.runs(status="done")[0].artefacts == {"report": "report.htm"}


def test_deleting_a_run_removes_its_artefacts(store):
    store.add_run(run(artefacts={"result": "result.json"}))
    store.delete_run("r1")
    with pytest.raises(NotFoundError):
        store.run("r1")
    store.add_run(run())
    assert store.run("r1").artefacts == {}


def test_unfinished_runs(store):
    store.add_run(run("a", 0, status=RunStatus.RUNNING))
    store.add_run(run("b", 1, status=RunStatus.DONE))
    store.add_run(run("c", 2))
    assert [r.id for r in store.unfinished_runs()] == ["a", "c"]


def test_a_run_needs_a_known_strategy(store):
    import sqlite3

    with pytest.raises(sqlite3.IntegrityError):
        store.add_run(run(strategy_hash="unknown"))

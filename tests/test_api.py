"""The API against a faked terminal: MetaEditor, both engines and the history measurement are
stand-ins, so these tests need no MetaTrader."""

import importlib.util
import inspect
import json
import threading
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from strategylab import api
from strategylab.api import Services, create_app, downsample, drawdown_points
from strategylab.compiler import CompileResult, Diagnostic, stage_upload
from strategylab.config import ConfigError, Settings, TerminalInstall
from strategylab.jobs import JobOutput
from strategylab.metrics import compute_metrics
from strategylab.result import BacktestResult, BalancePoint, Deal, Engine, RunMeta
from strategylab.store import RunRecord, RunSettings, RunStatus, Store, StrategyRecord

ROOT = Path(__file__).resolve().parents[1]
BASE_URL = "http://127.0.0.1:8000"

EA = b"""
input double Lots = 0.1;   // Lot size
input int    Period = 12;
void OnTick() {}
"""
SCRIPT = b"""
import time

import MetaTrader5 as mt5

FAST = 10  # fast period
"""


def epoch(*parts):
    return int(datetime(*parts, tzinfo=UTC).timestamp())


def measure(install, first_years):
    return "Demo-Server", [
        {
            "name": name,
            "description": "Euro vs US Dollar",
            "digits": 5,
            "path": "FX\\EURUSD@",
            "first_bar": epoch(2024, 1, 2),
            "last_bar": epoch(2025, 12, 31, 23, 59),
        }
        for name in first_years
    ]


def fake_compiler(upload, install, upload_name):
    staged = stage_upload(upload, install.strategies_dir, upload_name)
    if staged.prebuilt or b"BROKEN" not in staged.entry_path.read_bytes():
        return CompileResult(staged, ok=True, compiled=not staged.prebuilt, cached=False)
    error = Diagnostic("error", "'BROKEN' - undeclared identifier", 256, staged.entry, 4, 5)
    return CompileResult(staged, ok=False, compiled=True, cached=False, diagnostics=(error,))


def sample_result(ctx):
    t0 = datetime(2025, 1, 6, 10)

    def deal(ticket, minutes, type_, entry, price, profit):
        return Deal(
            ticket=ticket,
            server_time=t0 + timedelta(minutes=minutes),
            time=(t0 + timedelta(minutes=minutes, hours=-2)).replace(tzinfo=UTC),
            symbol=None if type_ == "balance" else ctx.run.settings.symbol,
            type=type_,
            entry=entry,
            volume=None if type_ == "balance" else 0.1,
            price=price,
            order=ticket,
            commission=0.0,
            swap=0.0,
            profit=profit,
            balance=None,
        )

    deals = [
        deal(1, 0, "balance", None, None, 10_000),
        deal(2, 1, "buy", "in", 1.1, 0),
        deal(3, 30, "sell", "out", 1.1125, 12.5),
        deal(4, 60, "sell", "in", 1.1, 0),
        deal(5, 90, "buy", "out", 1.105, -5.0),
    ]
    balance = [
        BalancePoint(server_time=d.server_time, time=d.time, balance=b)
        for d, b in zip([deals[0], deals[2], deals[4]], [10_000, 10_012.5, 10_007.5], strict=True)
    ]
    s = ctx.run.settings
    meta = RunMeta(
        engine=ctx.strategy.engine,
        fidelity="fake engine",
        strategy_name=ctx.run.strategy_name,
        strategy_hash=ctx.strategy.hash,
        symbol=s.symbol,
        timeframe=s.timeframe,
        date_from=s.date_from,
        date_to=s.date_to,
        model=s.model,
        deposit=s.deposit,
        currency=s.currency,
        leverage=s.leverage,
        parameters=s.parameters,
    )
    result = BacktestResult(meta=meta, deals=deals, balance=balance)
    result.metrics = compute_metrics(deals)
    return result


class FakeExecutor:
    """Steps through the stages; holds in "running" until released or cancelled."""

    def __init__(self):
        self.release = threading.Event()
        self.release.set()
        self.started = threading.Event()
        self.executed: list[str] = []
        self.running = 0
        self.most_at_once = 0
        self._lock = threading.Lock()

    def execute(self, ctx):
        with self._lock:
            self.running += 1
            self.most_at_once = max(self.most_at_once, self.running)
            self.executed.append(ctx.run.id)
        try:
            ctx.stage("compiling")
            ctx.log("compile", "0 errors, 0 warnings")
            ctx.stage("running")
            ctx.log("agent", "testing started")
            self.started.set()
            while not self.release.wait(0.01):
                if ctx.cancel.is_set():
                    return JobOutput(RunStatus.CANCELLED, "cancelled", "Cancelled; closed.")
            ctx.stage("parsing")
            return JobOutput(RunStatus.DONE, "success", "2 trades.", result=sample_result(ctx))
        finally:
            with self._lock:
                self.running -= 1


@pytest.fixture
def terminal(tmp_path):
    root = tmp_path / "mt5"
    base = root / "bases" / "Demo-Server"
    for year in (2024, 2025):
        path = base / "history" / "EURUSD@" / f"{year}.hcc"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
    ticks = base / "ticks" / "EURUSD@" / "202503.tkc"
    ticks.parent.mkdir(parents=True)
    ticks.write_bytes(b"x")
    (root / "MQL5" / "Experts").mkdir(parents=True)
    return TerminalInstall(install_dir=root, data_dir=root, portable=True)


@pytest.fixture
def settings(tmp_path, terminal):
    return Settings(
        terminal=terminal,
        source="test",
        account_server="Demo-Server",
        workspace_dir=tmp_path / "workspace",
    )


@pytest.fixture
def running_terminals(monkeypatch):
    pids: list = []
    monkeypatch.setattr(api, "terminals_using", lambda install: pids)
    return pids


def make_app(settings, executor=None, **changes):
    services = Services(
        settings_loader=lambda: settings,
        compiler=fake_compiler,
        executor=executor or FakeExecutor(),
        measure=measure,
        **changes,
    )
    return create_app(services)


@pytest.fixture
def executor():
    return FakeExecutor()


@pytest.fixture
def client(settings, executor, running_terminals):
    with TestClient(make_app(settings, executor), base_url=BASE_URL) as test_client:
        yield test_client


def upload(client, name, data, set_file=None):
    files = {"file": (name, data)}
    if set_file is not None:
        files["set_file"] = set_file
    return client.post("/api/strategies", files=files)


def wait_for(client, run_id, *statuses, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = client.get(f"/api/runs/{run_id}").json()["run"]
        if run["status"] in statuses:
            return run
        time.sleep(0.02)
    raise AssertionError(f"run {run_id} is {run['status']}, not {statuses}")


def run_body(strategy_hash, **changes):
    body = {
        "strategy_hash": strategy_hash,
        "symbol": "EURUSD@",
        "timeframe": "H1",
        "date_from": "2025-01-01",
        "date_to": "2025-02-01",
    }
    body.update(changes)
    return body


@pytest.fixture
def ea(client):
    return upload(client, "Cross.mq5", EA).json()


@pytest.fixture
def script(client):
    return upload(client, "cross.py", SCRIPT).json()


# --- binding -----------------------------------------------------------------------------------


def test_the_server_binds_to_loopback_only(monkeypatch):
    import uvicorn

    captured = {}
    monkeypatch.setattr(api, "create_app", lambda: "app")
    monkeypatch.setattr(uvicorn, "run", lambda app, **options: captured.update(options))
    api.main()
    assert captured["host"] == "127.0.0.1"
    assert api.HOST == "127.0.0.1"


def test_the_dev_server_binds_to_loopback_only():
    spec = importlib.util.spec_from_file_location("project_tasks", ROOT / "tasks.py")
    tasks = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tasks)
    assert tasks.BACKEND_HOST == "127.0.0.1"
    assert '"--host",\n        BACKEND_HOST' in inspect.getsource(tasks.dev)


def test_a_request_for_another_host_name_is_refused(client):
    response = client.get("/api/health", headers={"host": "rebind.example.com:8000"})
    assert response.status_code == 421
    assert client.get("/api/health", headers={"host": "localhost:5173"}).status_code == 200


def test_a_request_from_another_machine_is_refused(settings, running_terminals):
    app = make_app(settings, start_worker=False)
    with TestClient(app, base_url=BASE_URL, client=("192.168.1.20", 50000)) as remote:
        assert remote.get("/api/health").status_code == 403


# --- health ------------------------------------------------------------------------------------


def test_health_reports_the_terminal(client):
    health = client.get("/api/health").json()
    assert health["status"] == "ok"
    assert health["terminal"]["found"] is True
    assert health["terminal"]["server"] == "Demo-Server"
    assert health["queue"] == {"current": None, "queued": 0}


def test_health_says_when_the_terminal_is_not_found(tmp_path, running_terminals):
    def missing():
        raise ConfigError("No MetaTrader 5 terminal was found. Set [terminal] path in local.toml.")

    app = create_app(Services(settings_loader=missing, workspace_dir=tmp_path / "w"))
    with TestClient(app, base_url=BASE_URL) as client:
        health = client.get("/api/health").json()
        assert health["status"] == "unavailable"
        assert "local.toml" in health["message"]
        response = upload(client, "Cross.mq5", EA)
        assert response.status_code == 503
        assert "No MetaTrader 5 terminal" in response.json()["detail"]


def test_health_says_when_someone_else_has_the_terminal_open(client, running_terminals):
    running_terminals.append(type("Running", (), {"pid": 4321})())
    health = client.get("/api/health").json()
    assert health["status"] == "attention"
    assert health["terminal"]["running_pids"] == [4321]
    assert "already running" in health["message"]


# --- uploads -----------------------------------------------------------------------------------


def test_an_mql5_upload_is_compiled_and_its_inputs_read(client):
    response = upload(client, "Cross.mq5", EA)
    assert response.status_code == 201
    strategy = response.json()
    assert (strategy["kind"], strategy["engine"], strategy["ok"]) == ("mq5", "mt5_tester", True)
    assert [(p["name"], p["kind"], p["default"], p["label"]) for p in strategy["parameters"]] == [
        ("Lots", "real", "0.1", "Lot size"),
        ("Period", "integer", "12", None),
    ]
    assert client.get("/api/strategies").json()[0]["hash"] == strategy["hash"]


def test_compile_errors_come_back_with_file_and_line(client):
    strategy = upload(client, "Broken.mq5", EA + b"\nBROKEN\n").json()
    assert strategy["ok"] is False
    (error,) = strategy["diagnostics"]
    assert (error["file"], error["line"], error["column"], error["code"]) == (
        "Broken.mq5",
        4,
        5,
        256,
    )


def test_a_python_upload_is_checked_without_running_it(client):
    strategy = upload(client, "cross.py", SCRIPT).json()
    assert (strategy["kind"], strategy["engine"], strategy["ok"]) == ("py", "python_sim", True)
    assert [(p["name"], p["default"], p["label"]) for p in strategy["parameters"]] == [
        ("FAST", "10", "fast period")
    ]
    broken = upload(client, "broken.py", b"import MetaTrader5\nif True\n    pass\n").json()
    assert broken["ok"] is False
    assert broken["diagnostics"][0]["line"] == 2


def test_a_compiled_expert_takes_its_inputs_from_a_set_file(client):
    set_text = "﻿Lots=0.2\r\nPeriod=30\r\n".encode("utf-16-le")
    strategy = upload(client, "Cross.ex5", b"MQL5 binary", set_file=("Cross.set", set_text)).json()
    assert strategy["kind"] == "zip"
    assert strategy["parameter_source"] == "set_file"
    assert [(p["name"], p["default"]) for p in strategy["parameters"]] == [
        ("Lots", "0.2"),
        ("Period", "30"),
    ]
    bare = upload(client, "Other.ex5", b"MQL5 binary 2").json()
    assert bare["parameter_source"] == "none"
    assert any(".set file" in note for note in bare["notes"])


@pytest.mark.parametrize(
    ("name", "data", "status", "message"),
    [
        ("notes.txt", b"hello", 415, "not a strategy StrategyLab can run"),
        ("Cross.mq5", b"", 400, "is empty"),
        ("bundle.zip", b"not a zip", 400, "zip"),
    ],
)
def test_bad_uploads_are_refused_with_a_reason(client, name, data, status, message):
    response = upload(client, name, data)
    assert response.status_code == status
    assert message in response.json()["detail"]


def test_a_set_file_needs_a_compiled_expert(client):
    response = upload(client, "Cross.mq5", EA, set_file=("Cross.set", b"Lots=1\r\n"))
    assert response.status_code == 400
    assert "only needed with a compiled .ex5" in response.json()["detail"]


# --- symbols -----------------------------------------------------------------------------------


def test_symbols_list_what_has_history_and_its_dates(client):
    found = client.get("/api/symbols").json()
    assert found["server"] == "Demo-Server"
    (symbol,) = found["symbols"]
    assert symbol["name"] == "EURUSD@"
    assert (symbol["bars_from"], symbol["bars_to"]) == ("2024-01-02", "2025-12-31")
    assert symbol["history_years"] == [2024, 2025]
    assert symbol["ticks_from"] == "2025-03-01"
    assert symbol["measured"] is True


# --- runs and the state machine ----------------------------------------------------------------


def states(stream_text):
    found = []
    for block in stream_text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        if lines.get("event") == "state":
            data = json.loads(lines["data"])
            if not data.get("snapshot"):
                found.append((int(lines["id"]), data["status"]))
    return found


def test_a_run_goes_through_every_state_and_its_results_are_served(client, ea):
    run = client.post("/api/runs", json=run_body(ea["hash"], parameters={"Lots": 0.2})).json()
    assert run["status"] == "queued"
    assert run["settings"]["parameters"] == {"Lots": "0.2"}
    done = wait_for(client, run["id"], "done")
    assert done["outcome"] == "success"
    assert done["trade_count"] == 2
    assert done["metrics"]["net_profit"] == pytest.approx(7.5)
    assert set(done["stage_seconds"]) == {"compiling", "running", "parsing"}

    stream = client.get(f"/api/runs/{run['id']}/events").text
    assert [status for _, status in states(stream)] == [
        "queued",
        "compiling",
        "running",
        "parsing",
        "done",
    ]
    assert '"line": "testing started"' in stream

    running_seq = next(seq for seq, status in states(stream) if status == "running")
    resumed = client.get(
        f"/api/runs/{run['id']}/events", headers={"Last-Event-ID": str(running_seq)}
    ).text
    assert [status for _, status in states(resumed)] == ["parsing", "done"]

    deals = client.get(f"/api/runs/{run['id']}/deals").json()
    assert deals["total"] == 5
    assert deals["deals"][2]["profit"] == 12.5
    assert deals["deals"][0]["time"].endswith("+00:00")
    series = client.get(f"/api/runs/{run['id']}/equity").json()
    assert [p["balance"] for p in series["points"]] == [10_000, 10_012.5, 10_007.5]
    assert series["points"][2]["drawdown"] == pytest.approx(5.0)
    summary = client.get(f"/api/runs/{run['id']}/result").json()
    assert summary["meta"]["fidelity"] == "fake engine"
    assert client.get(f"/api/runs/{run['id']}/report/").status_code == 404


def test_runs_go_one_at_a_time_in_order(client, executor, ea):
    executor.release.clear()
    ids = [client.post("/api/runs", json=run_body(ea["hash"])).json()["id"] for _ in range(3)]
    assert executor.started.wait(5)
    detail = [client.get(f"/api/runs/{run_id}").json() for run_id in ids]
    assert [d["run"]["status"] for d in detail] == ["running", "queued", "queued"]
    assert [d["queue_position"] for d in detail] == [None, 1, 2]
    assert client.get("/api/health").json()["queue"] == {"current": ids[0], "queued": 2}
    executor.release.set()
    for run_id in ids:
        wait_for(client, run_id, "done")
    assert executor.executed == ids
    assert executor.most_at_once == 1


def test_listing_runs_filters_them(client, ea, script):
    a = client.post("/api/runs", json=run_body(ea["hash"])).json()["id"]
    b = client.post("/api/runs", json=run_body(script["hash"], timeframe="M15")).json()["id"]
    wait_for(client, a, "done")
    wait_for(client, b, "done")
    assert [r["id"] for r in client.get("/api/runs").json()] == [b, a]
    assert [r["id"] for r in client.get("/api/runs?engine=python_sim").json()] == [b]
    assert [r["id"] for r in client.get(f"/api/runs?strategy={ea['hash']}").json()] == [a]
    detail = client.get(f"/api/strategies/{ea['hash']}").json()
    assert [r["id"] for r in detail["runs"]] == [a]


# --- cancelling and deleting -------------------------------------------------------------------


def test_cancelling_a_running_run_stops_the_engine(client, executor, ea):
    executor.release.clear()
    run_id = client.post("/api/runs", json=run_body(ea["hash"])).json()["id"]
    assert executor.started.wait(5)
    assert client.post(f"/api/runs/{run_id}/cancel").status_code == 202
    cancelled = wait_for(client, run_id, "cancelled")
    assert cancelled["message"] == "Cancelled; closed."
    assert client.post(f"/api/runs/{run_id}/cancel").status_code == 409


def test_cancelling_a_queued_run_means_it_never_runs(client, executor, ea):
    executor.release.clear()
    first = client.post("/api/runs", json=run_body(ea["hash"])).json()["id"]
    second = client.post("/api/runs", json=run_body(ea["hash"])).json()["id"]
    assert executor.started.wait(5)
    cancelled = client.post(f"/api/runs/{second}/cancel").json()
    assert cancelled["status"] == "cancelled"
    executor.release.set()
    wait_for(client, first, "done")
    assert executor.executed == [first]
    assert client.get(f"/api/runs/{second}").json()["run"]["status"] == "cancelled"


def test_a_run_is_deleted_only_once_it_has_ended(client, executor, ea, settings):
    executor.release.clear()
    run_id = client.post("/api/runs", json=run_body(ea["hash"])).json()["id"]
    assert executor.started.wait(5)
    refused = client.delete(f"/api/runs/{run_id}")
    assert refused.status_code == 409
    assert "cancel it" in refused.json()["detail"]
    executor.release.set()
    wait_for(client, run_id, "done")
    run_dir = settings.workspace_dir / "runs" / run_id
    assert (run_dir / "deals.parquet").is_file()
    assert client.delete(f"/api/runs/{run_id}").status_code == 204
    assert not run_dir.exists()
    assert client.get(f"/api/runs/{run_id}").status_code == 404


# --- restart -----------------------------------------------------------------------------------


def test_runs_left_unfinished_by_a_previous_server_are_failed(settings, running_terminals):
    store = Store(settings.workspace_dir / "strategylab.sqlite")
    store.save_strategy(
        StrategyRecord(
            hash="h1",
            name="Cross.mq5",
            kind="mq5",
            engine=Engine.MT5_TESTER,
            folder="x",
            entry="Cross.mq5",
            parameter_source="source",
            ok=True,
            uploaded_at=datetime.now(UTC),
        )
    )
    created = datetime.now(UTC)
    for index, status in enumerate((RunStatus.RUNNING, RunStatus.QUEUED, RunStatus.DONE)):
        store.add_run(
            RunRecord(
                id=f"r{index}",
                strategy_hash="h1",
                strategy_name="Cross",
                engine=Engine.MT5_TESTER,
                status=status,
                settings=RunSettings(
                    symbol="EURUSD@",
                    timeframe="H1",
                    date_from=date(2025, 1, 1),
                    date_to=date(2025, 2, 1),
                ),
                created_at=created + timedelta(seconds=index),
            )
        )
    executor = FakeExecutor()
    with TestClient(make_app(settings, executor), base_url=BASE_URL) as client:
        runs = {r["id"]: r for r in client.get("/api/runs").json()}
    assert runs["r0"]["status"] == "failed"
    assert "stopped while this run was running" in runs["r0"]["message"]
    assert runs["r1"]["status"] == "failed"
    assert "before this run started" in runs["r1"]["message"]
    assert runs["r2"]["status"] == "done"
    assert executor.executed == []


# --- validation --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"symbol": "GBPUSD@"}, "no history for GBPUSD@. Symbols with history: EURUSD@"),
        ({"date_from": "2023-06-01"}, "covers 2024-01-02 to 2025-12-31"),
        ({"date_to": "2031-01-01"}, "covers 2024-01-02 to 2025-12-31"),
        ({"date_to": "2024-12-01"}, "end date must be after the start date"),
        ({"timeframe": "H7"}, "not a tester timeframe"),
        ({"parameters": {"Risk": 1}}, "no inputs named Risk"),
        ({"parameters": {"Period": "fast"}}, "Period needs a whole number"),
    ],
)
def test_an_mql5_run_is_checked_before_it_is_queued(client, ea, changes, message):
    response = client.post("/api/runs", json=run_body(ea["hash"], **changes))
    assert response.status_code == 422
    assert message in response.json()["detail"]
    assert client.get("/api/runs").json() == []


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"timeframe": "M2"}, "Python strategies run on M1, M5"),
        ({"model": "every_tick"}, "1-minute bars (ohlc_m1) or recorded ticks"),
        ({"model": "real_ticks"}, "Recorded ticks for EURUSD@ start in March 2025"),
        ({"parameters": {"FAST": "x"}}, "FAST needs a whole number"),
    ],
)
def test_a_python_run_is_checked_before_it_is_queued(client, script, changes, message):
    response = client.post("/api/runs", json=run_body(script["hash"], **changes))
    assert response.status_code == 422
    assert message in response.json()["detail"]


def test_a_strategy_with_errors_cannot_run(client):
    broken = upload(client, "Broken.mq5", EA + b"\nBROKEN\n").json()
    response = client.post("/api/runs", json=run_body(broken["hash"]))
    assert response.status_code == 422
    assert "has errors" in response.json()["detail"]


def test_an_unknown_strategy_or_run_is_404(client):
    assert client.post("/api/runs", json=run_body("nope")).status_code == 404
    assert client.get("/api/runs/nope").status_code == 404
    assert client.get("/api/strategies/nope").status_code == 404


# --- rerun -------------------------------------------------------------------------------------


def test_rerun_with_changed_parameters(client, ea):
    first = client.post("/api/runs", json=run_body(ea["hash"], parameters={"Lots": 0.2})).json()
    wait_for(client, first["id"], "done")
    response = client.post(
        f"/api/runs/{first['id']}/rerun", json={"parameters": {"Period": 20}, "deposit": 5000}
    )
    assert response.status_code == 201
    second = response.json()
    assert second["rerun_of"] == first["id"]
    assert second["settings"]["parameters"] == {"Lots": "0.2", "Period": "20"}
    assert second["settings"]["deposit"] == 5000
    assert second["settings"]["symbol"] == "EURUSD@"
    bad = client.post(f"/api/runs/{first['id']}/rerun", json={"parameters": {"Period": "x"}})
    assert bad.status_code == 422


# --- downsampling ------------------------------------------------------------------------------


def test_downsampling_keeps_the_peak_and_the_worst_drawdown():
    rng = np.random.default_rng(7)
    balance = 10_000 + np.cumsum(rng.normal(0, 10, 20_000))
    balance[12_345] = balance.max() + 500  # a lone spike
    frame = drawdown_points(
        pd.DataFrame(
            {
                "server_time": pd.date_range("2025-01-01", periods=len(balance), freq="min"),
                "balance": balance,
            }
        )
    )
    small = downsample(frame, 600)
    assert len(small) <= 600 + 2
    assert small.index[0] == 0 and small.index[-1] == len(frame) - 1
    assert frame["balance"].idxmax() in small.index
    assert frame["drawdown"].idxmax() in small.index
    assert small["server_time"].is_monotonic_increasing
    assert downsample(frame.head(50), 600) is not None
    assert len(downsample(frame.head(50), 600)) == 50


# --- charts ------------------------------------------------------------------------------------


def test_a_done_run_serves_its_price_bars_in_windows(settings, running_terminals):
    from strategylab.charts import aggregate

    def exporter(settings_, symbol, timeframe, start, end):
        base = epoch(2025, 1, 1)
        rows = [(base + 3600 * i, 1.1, 1.2, 1.0, 1.15) for i in range(24 * 31)]
        frame = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close"])
        return aggregate(frame, timeframe)

    app = make_app(settings, bar_exporter=exporter)
    with TestClient(app, base_url=BASE_URL) as client:
        ea = upload(client, "Cross.mq5", EA).json()
        run_id = client.post("/api/runs", json=run_body(ea["hash"])).json()["id"]
        wait_for(client, run_id, "done")
        latest = client.get(f"/api/runs/{run_id}/bars?count=24").json()
        assert latest["timeframe"] == "H1"
        assert latest["total"] == 24 * 31
        assert len(latest["bars"]) == 24
        assert latest["bars"][-1]["time"] == epoch(2025, 1, 31, 23)
        around = client.get(f"/api/runs/{run_id}/bars?around={epoch(2025, 1, 10, 12)}&count=4")
        assert [b["time"] for b in around.json()["bars"]] == [
            epoch(2025, 1, 10, h) for h in (10, 11, 12, 13)
        ]
        definitions = client.get("/api/metrics").json()
        assert "net_profit" in definitions and "sharpe_ratio" in definitions

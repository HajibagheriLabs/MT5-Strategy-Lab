"""Both engines through the API, on the real terminal."""

import dataclasses
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from strategylab.api import Services, create_app
from strategylab.config import load_settings

pytestmark = pytest.mark.mt5
ROOT = Path(__file__).resolve().parents[1]


def wait_until_finished(client, run_id, timeout=900):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = client.get(f"/api/runs/{run_id}").json()["run"]
        if run["status"] in ("done", "failed", "cancelled"):
            return run
        time.sleep(1)
    raise AssertionError(f"run {run_id} did not finish")


def test_an_mql5_and_a_python_run_through_the_api(tmp_path):
    settings = load_settings()
    # A workspace of its own, so the test leaves the real run history alone.
    settings = dataclasses.replace(settings, workspace_dir=tmp_path / "workspace")
    app = create_app(Services(settings_loader=lambda: settings))
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        assert client.get("/api/health").json()["status"] == "ok"
        names = [s["name"] for s in client.get("/api/symbols").json()["symbols"]]
        symbol = next((n for n in names if n.upper().startswith("EURUSD")), None)
        if symbol is None:
            pytest.skip("no EURUSD history in the terminal yet")

        examples = settings.terminal.experts_dir / "Examples" / "Moving Average"
        source = (examples / "Moving Average.mq5").read_bytes()
        ea = client.post("/api/strategies", files={"file": ("Moving Average.mq5", source)}).json()
        assert ea["ok"], ea["diagnostics"]
        script = (ROOT / "samples" / "python" / "ma_cross.py").read_bytes()
        py = client.post("/api/strategies", files={"file": ("ma_cross.py", script)}).json()
        assert py["ok"], py["diagnostics"]

        window = {"symbol": symbol, "timeframe": "H1", "date_from": "2025-03-01"}
        runs = [
            client.post(
                "/api/runs",
                json={"strategy_hash": ea["hash"], "date_to": "2025-04-01", **window},
            ).json(),
            client.post(
                "/api/runs",
                json={
                    "strategy_hash": py["hash"],
                    "date_to": "2025-05-01",
                    "parameters": {"FAST_PERIOD": 10},
                    **window,
                },
            ).json(),
        ]
        for run, engine in zip(runs, ("mt5_tester", "python_sim"), strict=True):
            finished = wait_until_finished(client, run["id"])
            assert finished["status"] == "done", finished["message"]
            assert finished["engine"] == engine
            assert finished["trade_count"] > 0
            expected = "MetaTrader 5" if engine == "mt5_tester" else "Simulated by StrategyLab"
            assert finished["fidelity"].startswith(expected)
            stream = client.get(f"/api/runs/{run['id']}/events").text
            seen = [
                json.loads(line[len("data: ") :])["status"]
                for line in stream.splitlines()
                if line.startswith("data: ") and '"status"' in line
            ]
            assert seen[-4:] == ["compiling", "running", "parsing", "done"]
            assert client.get(f"/api/runs/{run['id']}/deals").json()["total"] > 0
            assert client.get(f"/api/runs/{run['id']}/equity").json()["points"]
        tester_run = runs[0]["id"]
        assert client.get(f"/api/runs/{tester_run}/report/").status_code == 200
        assert {s["source"] for s in client.get(f"/api/runs/{tester_run}/logs").json()} >= {
            "terminal",
            "agent",
        }

"""SQLite run history: strategies, runs, and the files each run left behind.

One database file under the workspace. Every call opens its own connection, so the API's
threads and the job worker never share one; WAL mode lets readers carry on while the worker
writes.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from strategylab.result import Engine, TickModel

StrategyKind = Literal["mq5", "ex5", "zip", "py"]
Granularity = Literal["m1_ohlc", "ticks"]


class RunStatus(StrEnum):
    QUEUED = "queued"
    COMPILING = "compiling"
    RUNNING = "running"
    PARSING = "parsing"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def finished(self) -> bool:
        return self in (RunStatus.DONE, RunStatus.FAILED, RunStatus.CANCELLED)


ACTIVE = (RunStatus.COMPILING, RunStatus.RUNNING, RunStatus.PARSING)


class Diagnostic(BaseModel):
    severity: Literal["error", "warning"]
    message: str
    code: int | None = None
    file: str | None = None
    line: int | None = None
    column: int | None = None


class ParameterOption(BaseModel):
    value: str
    label: str


class Parameter(BaseModel):
    """An input a strategy declares, the same shape for MQL5 and Python strategies."""

    name: str
    kind: Literal["bool", "integer", "real", "string", "datetime", "color", "enum", "unknown"]
    default: str
    """The default as a run would pass it: numbers as written, booleans as true/false, MQL5
    enums as their integer value."""
    label: str | None = None
    group: str | None = None
    options: list[ParameterOption] = Field(default_factory=list)
    source: Literal["input", "sinput", "set_file", "constant", "option"]
    type_name: str | None = None
    """The declared type as written (`double`, `ENUM_TIMEFRAMES`), where there is one."""


class StrategyRecord(BaseModel):
    hash: str
    name: str
    kind: StrategyKind
    engine: Engine
    folder: str
    entry: str
    parameters: list[Parameter] = Field(default_factory=list)
    parameter_source: Literal["source", "set_file", "none", "script"]
    ok: bool
    """Compiled (MQL5) or parsed (Python) without errors, so it can be run."""
    diagnostics: list[Diagnostic] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    uploaded_at: datetime


class RunSettings(BaseModel):
    symbol: str
    timeframe: str
    date_from: date
    date_to: date
    """Exclusive, as in the Strategy Tester."""
    model: TickModel = TickModel.OHLC_M1
    """The tester's tick model; Python runs record theirs here too (ohlc_m1 or real_ticks)."""
    deposit: float = 10_000.0
    currency: str = "USD"
    leverage: int = 100
    parameters: dict[str, str] = Field(default_factory=dict)
    commission_per_lot: float = 0.0
    timeout_s: float | None = None


class RunRecord(BaseModel):
    id: str
    strategy_hash: str
    strategy_name: str
    engine: Engine
    status: RunStatus
    settings: RunSettings
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    stage_seconds: dict[str, float] = Field(default_factory=dict)
    outcome: str | None = None
    """The engine's own word for how it ended: success, zero_trades, timeout, no_report..."""
    message: str | None = None
    error: str | None = None
    metrics: dict[str, float | int | None] = Field(default_factory=dict)
    fidelity: str | None = None
    trade_count: int | None = None
    rerun_of: str | None = None
    artefacts: dict[str, str] = Field(default_factory=dict)


SCHEMA = """
CREATE TABLE IF NOT EXISTS strategies (
    hash TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    engine TEXT NOT NULL,
    folder TEXT NOT NULL,
    entry TEXT NOT NULL,
    parameters TEXT NOT NULL,
    parameter_source TEXT NOT NULL,
    ok INTEGER NOT NULL,
    diagnostics TEXT NOT NULL,
    notes TEXT NOT NULL,
    uploaded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    strategy_hash TEXT NOT NULL REFERENCES strategies(hash),
    strategy_name TEXT NOT NULL,
    engine TEXT NOT NULL,
    status TEXT NOT NULL,
    settings TEXT NOT NULL,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    stage_seconds TEXT NOT NULL DEFAULT '{}',
    outcome TEXT,
    message TEXT,
    error TEXT,
    metrics TEXT NOT NULL DEFAULT '{}',
    fidelity TEXT,
    trade_count INTEGER,
    rerun_of TEXT
);
CREATE INDEX IF NOT EXISTS runs_by_strategy ON runs(strategy_hash, created_at);
CREATE INDEX IF NOT EXISTS runs_by_status ON runs(status);
CREATE TABLE IF NOT EXISTS artefacts (
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    path TEXT NOT NULL,
    PRIMARY KEY (run_id, kind)
);
"""

_JSON_RUN_FIELDS = ("settings", "stage_seconds", "metrics")
_RUN_COLUMNS = (
    "id", "strategy_hash", "strategy_name", "engine", "status", "settings", "created_at",
    "started_at", "finished_at", "stage_seconds", "outcome", "message", "error", "metrics",
    "fidelity", "trade_count", "rerun_of",
)  # fmt: skip


def now() -> datetime:
    return datetime.now(UTC)


class NotFoundError(LookupError):
    pass


class Store:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    # --- strategies ----------------------------------------------------------------------------

    def save_strategy(self, record: StrategyRecord) -> StrategyRecord:
        """Insert or refresh a strategy; uploading the same content again keeps its first name."""
        data = record.model_dump(mode="json")
        with self._connect() as db:
            # An upsert, not a replace: replacing deletes the row, which runs refer to.
            db.execute(
                "INSERT INTO strategies VALUES "
                "(:hash, :name, :kind, :engine, :folder, :entry, :parameters, "
                ":parameter_source, :ok, :diagnostics, :notes, :uploaded_at) "
                "ON CONFLICT(hash) DO UPDATE SET kind = excluded.kind, engine = excluded.engine, "
                "folder = excluded.folder, entry = excluded.entry, "
                "parameters = excluded.parameters, parameter_source = excluded.parameter_source, "
                "ok = excluded.ok, diagnostics = excluded.diagnostics, notes = excluded.notes",
                {
                    **data,
                    "parameters": json.dumps(data["parameters"]),
                    "diagnostics": json.dumps(data["diagnostics"]),
                    "notes": json.dumps(data["notes"]),
                    "ok": int(record.ok),
                },
            )
        return self.strategy(record.hash)

    def strategy(self, strategy_hash: str) -> StrategyRecord:
        with self._connect() as db:
            row = db.execute("SELECT * FROM strategies WHERE hash = ?", (strategy_hash,)).fetchone()
        if row is None:
            raise NotFoundError(f"No strategy {strategy_hash}.")
        return _strategy(row)

    def strategies(self) -> list[StrategyRecord]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM strategies ORDER BY uploaded_at DESC").fetchall()
        return [_strategy(row) for row in rows]

    # --- runs ----------------------------------------------------------------------------------

    def add_run(self, record: RunRecord) -> RunRecord:
        data = _run_row(record)
        with self._connect() as db:
            db.execute(
                f"INSERT INTO runs ({', '.join(_RUN_COLUMNS)}) "
                f"VALUES ({', '.join(':' + c for c in _RUN_COLUMNS)})",
                data,
            )
            for kind, path in record.artefacts.items():
                db.execute("INSERT INTO artefacts VALUES (?, ?, ?)", (record.id, kind, path))
        return self.run(record.id)

    def update_run(self, run_id: str, **fields: Any) -> RunRecord:
        if not fields:
            return self.run(run_id)
        unknown = set(fields) - set(_RUN_COLUMNS)
        if unknown:
            raise ValueError(f"Not run fields: {sorted(unknown)}")
        values = {key: _column_value(key, value) for key, value in fields.items()}
        with self._connect() as db:
            cursor = db.execute(
                f"UPDATE runs SET {', '.join(f'{k} = :{k}' for k in values)} WHERE id = :run_id",
                {**values, "run_id": run_id},
            )
            if cursor.rowcount == 0:
                raise NotFoundError(f"No run {run_id}.")
        return self.run(run_id)

    def set_artefacts(self, run_id: str, artefacts: Mapping[str, Path | str]) -> None:
        with self._connect() as db:
            for kind, path in artefacts.items():
                db.execute(
                    "INSERT OR REPLACE INTO artefacts VALUES (?, ?, ?)", (run_id, kind, str(path))
                )

    def run(self, run_id: str) -> RunRecord:
        with self._connect() as db:
            row = db.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
            if row is None:
                raise NotFoundError(f"No run {run_id}.")
            artefacts = db.execute(
                "SELECT kind, path FROM artefacts WHERE run_id = ?", (run_id,)
            ).fetchall()
        return _run(row, {a["kind"]: a["path"] for a in artefacts})

    def runs(
        self,
        *,
        status: str | None = None,
        engine: str | None = None,
        strategy_hash: str | None = None,
        symbol: str | None = None,
        limit: int = 500,
        offset: int = 0,
    ) -> list[RunRecord]:
        where, values = [], []
        if status:
            where.append("status = ?")
            values.append(status)
        if engine:
            where.append("engine = ?")
            values.append(engine)
        if strategy_hash:
            where.append("strategy_hash = ?")
            values.append(strategy_hash)
        if symbol:
            where.append("json_extract(settings, '$.symbol') = ?")
            values.append(symbol)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        with self._connect() as db:
            rows = db.execute(
                f"SELECT * FROM runs {clause} ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
                (*values, limit, offset),
            ).fetchall()
            ids = [row["id"] for row in rows]
            artefacts: dict[str, dict[str, str]] = {run_id: {} for run_id in ids}
            if ids:
                marks = ", ".join("?" * len(ids))
                for item in db.execute(
                    f"SELECT run_id, kind, path FROM artefacts WHERE run_id IN ({marks})", ids
                ):
                    artefacts[item["run_id"]][item["kind"]] = item["path"]
        return [_run(row, artefacts[row["id"]]) for row in rows]

    def delete_run(self, run_id: str) -> None:
        with self._connect() as db:
            cursor = db.execute("DELETE FROM runs WHERE id = ?", (run_id,))
            if cursor.rowcount == 0:
                raise NotFoundError(f"No run {run_id}.")

    def unfinished_runs(self) -> list[RunRecord]:
        statuses = [RunStatus.QUEUED, *ACTIVE]
        marks = ", ".join("?" * len(statuses))
        with self._connect() as db:
            ids = [
                row["id"]
                for row in db.execute(
                    f"SELECT id FROM runs WHERE status IN ({marks}) ORDER BY created_at",
                    [str(s) for s in statuses],
                )
            ]
        return [self.run(run_id) for run_id in ids]


def _column_value(key: str, value: Any) -> Any:
    if key in _JSON_RUN_FIELDS:
        if isinstance(value, BaseModel):
            return value.model_dump_json()
        return json.dumps(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, StrEnum):
        return str(value)
    return value


def _run_row(record: RunRecord) -> dict[str, Any]:
    data = record.model_dump(mode="json", exclude={"artefacts"})
    for key in _JSON_RUN_FIELDS:
        data[key] = json.dumps(data[key])
    return data


def _run(row: sqlite3.Row, artefacts: dict[str, str]) -> RunRecord:
    data = dict(row)
    for key in _JSON_RUN_FIELDS:
        data[key] = json.loads(data[key]) if data[key] else {}
    return RunRecord.model_validate({**data, "artefacts": artefacts})


def _strategy(row: sqlite3.Row) -> StrategyRecord:
    data = dict(row)
    for key in ("parameters", "diagnostics", "notes"):
        data[key] = json.loads(data[key])
    data["ok"] = bool(data["ok"])
    return StrategyRecord.model_validate(data)

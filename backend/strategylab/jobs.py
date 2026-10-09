"""Serial job queue: one terminal, one job at a time.

A single worker thread takes runs in the order they were submitted. While it runs one it holds
the terminal lock, which anything else that drives the terminal (measuring history for the
symbol list) also takes, so the tester, history exports and data connections never overlap.
A run moves queued -> compiling -> running -> parsing -> done, or ends failed or cancelled.
Every change of state, and every line a run logs, is published as a numbered event that the
API streams to the browser.
"""

from __future__ import annotations

import collections
import queue
import shutil
import threading
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

import pandas as pd

from strategylab.result import BacktestResult, Engine, TickModel
from strategylab.store import (
    NotFoundError,
    RunRecord,
    RunStatus,
    Store,
    StrategyRecord,
    now,
)

MAX_EVENTS = 50_000
STAGES = (RunStatus.COMPILING, RunStatus.RUNNING, RunStatus.PARSING)


class RunStateError(Exception):
    """The run is in a state that does not allow what was asked."""


# --- events ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Event:
    seq: int
    run_id: str
    kind: Literal["state", "log"]
    data: dict[str, Any]


class EventLog:
    """Recent events in memory, numbered, for any number of readers to follow.

    A reader remembers the last number it saw and asks for what came after, so a browser that
    reconnects (with Last-Event-ID) misses nothing that is still held.
    """

    def __init__(self, limit: int = MAX_EVENTS) -> None:
        self._events: collections.deque[Event] = collections.deque(maxlen=limit)
        self._seq = 0
        self._changed = threading.Condition()

    @property
    def last_seq(self) -> int:
        with self._changed:
            return self._seq

    def publish(self, run_id: str, kind: Literal["state", "log"], data: dict[str, Any]) -> Event:
        with self._changed:
            self._seq += 1
            event = Event(self._seq, run_id, kind, data)
            self._events.append(event)
            self._changed.notify_all()
        return event

    def since(self, seq: int, run_id: str | None = None) -> list[Event]:
        with self._changed:
            return [
                e for e in self._events if e.seq > seq and (run_id is None or e.run_id == run_id)
            ]

    def wait(self, seq: int, timeout: float) -> bool:
        """Block until there is an event after `seq`, or `timeout` seconds pass."""
        with self._changed:
            return self._changed.wait_for(lambda: self._seq > seq, timeout)


# --- running one job ---------------------------------------------------------------------------


@dataclass
class JobOutput:
    status: RunStatus
    """DONE, FAILED or CANCELLED."""
    outcome: str
    message: str
    error: str | None = None
    result: BacktestResult | None = None
    artefacts: dict[str, Path] = field(default_factory=dict)


class JobContext:
    """What an executor sees of the run it is executing."""

    def __init__(
        self, jobs: JobQueue, run: RunRecord, strategy: StrategyRecord, run_dir: Path
    ) -> None:
        self.run = run
        self.strategy = strategy
        self.run_dir = run_dir
        self.cancel = threading.Event()
        self.status = RunStatus.QUEUED
        self.stage_seconds: dict[str, float] = {}
        self._jobs = jobs
        self._stage_started = time.monotonic()

    def stage(self, name: str) -> None:
        status = RunStatus(name)
        if status not in STAGES or status == self.status:
            return
        self._close_stage()
        self.status = status
        self._jobs._set_status(self.run.id, status, self.stage_seconds)

    def _close_stage(self) -> None:
        if self.status in STAGES:
            spent = time.monotonic() - self._stage_started
            self.stage_seconds[str(self.status)] = round(
                self.stage_seconds.get(str(self.status), 0.0) + spent, 3
            )
        self._stage_started = time.monotonic()

    def log(self, source: str, line: str) -> None:
        self._jobs.events.publish(self.run.id, "log", {"source": source, "line": line})


class Executor(Protocol):
    def execute(self, ctx: JobContext) -> JobOutput: ...


# --- the queue ---------------------------------------------------------------------------------


class JobQueue:
    def __init__(
        self,
        store: Store,
        executor: Executor,
        runs_dir: Path,
        events: EventLog | None = None,
    ) -> None:
        self.store = store
        self.executor = executor
        self.runs_dir = runs_dir
        self.events = events or EventLog()
        self.terminal_lock = threading.Lock()
        self._pending: queue.Queue[str | None] = queue.Queue()
        self._lock = threading.RLock()
        self._current: JobContext | None = None
        self._worker: threading.Thread | None = None

    # --- lifecycle -----------------------------------------------------------------------------

    def recover(self) -> list[RunRecord]:
        """Fail the runs a previous server left unfinished; nothing is driving them now."""
        failed = []
        for run in self.store.unfinished_runs():
            if run.status is RunStatus.QUEUED:
                message = "The server stopped before this run started. Run it again."
            else:
                message = (
                    f"The server stopped while this run was {run.status}, so it never finished. "
                    "Run it again."
                )
            failed.append(self._finish(run.id, RunStatus.FAILED, "interrupted", message))
        return failed

    def start(self) -> list[RunRecord]:
        recovered = self.recover()
        self._worker = threading.Thread(target=self._work, name="strategylab-jobs", daemon=True)
        self._worker.start()
        return recovered

    def stop(self, timeout: float = 120.0) -> None:
        """Cancel the run in progress (closing the terminal) and stop the worker."""
        with self._lock:
            if self._current is not None:
                self._current.cancel.set()
        self._pending.put(None)
        if self._worker is not None:
            self._worker.join(timeout)

    # --- requests ------------------------------------------------------------------------------

    def submit(self, run: RunRecord) -> RunRecord:
        record = self.store.add_run(run.model_copy(update={"status": RunStatus.QUEUED}))
        self._publish_state(record)
        self._pending.put(record.id)
        return record

    def cancel(self, run_id: str) -> RunRecord:
        with self._lock:
            run = self.store.run(run_id)
            if run.status.finished:
                raise RunStateError(f"Run {run_id} has already {_ended(run.status)}.")
            current = self._current
            if current is not None and current.run.id == run_id:
                current.cancel.set()
                current.log("stage", "Cancelling: stopping the engine.")
                return run
            return self._finish(
                run_id, RunStatus.CANCELLED, "cancelled", "Cancelled before it ran."
            )

    def delete(self, run_id: str) -> None:
        with self._lock:
            run = self.store.run(run_id)
            if not run.status.finished:
                raise RunStateError(f"Run {run_id} is {run.status}; cancel it before deleting it.")
            self.store.delete_run(run_id)
        shutil.rmtree(self.runs_dir / run_id, ignore_errors=True)
        self.events.publish(run_id, "state", {"id": run_id, "status": "deleted"})

    @property
    def current(self) -> str | None:
        with self._lock:
            return self._current.run.id if self._current is not None else None

    def position(self, run_id: str) -> int | None:
        """1 for the next run to start, and so on; None if it is not waiting."""
        queued = [r.id for r in self.store.runs(status=RunStatus.QUEUED, limit=10_000)]
        queued.reverse()
        return queued.index(run_id) + 1 if run_id in queued else None

    # --- the worker ----------------------------------------------------------------------------

    def _work(self) -> None:
        while True:
            run_id = self._pending.get()
            if run_id is None:
                return
            try:
                run = self.store.run(run_id)
            except NotFoundError:
                continue
            if run.status is RunStatus.QUEUED:
                self._execute(run)

    def _execute(self, run: RunRecord) -> None:
        run_dir = self.runs_dir / run.id
        run_dir.mkdir(parents=True, exist_ok=True)
        with self._lock:
            try:
                strategy = self.store.strategy(run.strategy_hash)
            except NotFoundError:
                self._finish(run.id, RunStatus.FAILED, "error", "Its strategy no longer exists.")
                return
            ctx = JobContext(self, run, strategy, run_dir)
            self._current = ctx
            self.store.update_run(run.id, started_at=now())
        try:
            with self.terminal_lock:
                output = self.executor.execute(ctx)
        except Exception as exc:
            output = JobOutput(
                RunStatus.FAILED,
                "error",
                f"The run stopped on an unexpected error: {exc}",
                error=traceback.format_exc(),
            )
        ctx._close_stage()
        fields: dict[str, Any] = {"stage_seconds": ctx.stage_seconds}
        artefacts = dict(output.artefacts)
        if output.result is not None:
            artefacts.update(write_result_files(output.result, run_dir))
            fields.update(
                metrics=output.result.metrics,
                fidelity=output.result.meta.fidelity,
                trade_count=output.result.trade_count,
            )
        existing = {k: v for k, v in artefacts.items() if Path(v).exists()}
        with self._lock:
            self._current = None
            try:
                self.store.set_artefacts(run.id, existing)
                self._finish(
                    run.id, output.status, output.outcome, output.message, output.error, **fields
                )
            except NotFoundError:
                pass

    # --- state ---------------------------------------------------------------------------------

    def _set_status(self, run_id: str, status: RunStatus, stage_seconds: dict[str, float]) -> None:
        record = self.store.update_run(run_id, status=status, stage_seconds=stage_seconds)
        self._publish_state(record)

    def _finish(
        self,
        run_id: str,
        status: RunStatus,
        outcome: str,
        message: str,
        error: str | None = None,
        **fields: Any,
    ) -> RunRecord:
        record = self.store.update_run(
            run_id,
            status=status,
            outcome=outcome,
            message=message,
            error=error,
            finished_at=now(),
            **fields,
        )
        self._publish_state(record)
        return record

    def _publish_state(self, record: RunRecord) -> None:
        self.events.publish(
            record.id,
            "state",
            {
                "id": record.id,
                "status": str(record.status),
                "outcome": record.outcome,
                "message": record.message,
                "stage_seconds": record.stage_seconds,
            },
        )


def _ended(status: RunStatus) -> str:
    return {"done": "finished", "failed": "failed", "cancelled": "been cancelled"}[str(status)]


def write_result_files(result: BacktestResult, run_dir: Path) -> dict[str, Path]:
    """The normalised result as JSON, and its deals table and balance series as parquet, which
    the API reads for tables and charts."""
    paths = {
        "result": run_dir / "result.json",
        "deals": run_dir / "deals.parquet",
        "equity": run_dir / "equity.parquet",
    }
    paths["result"].write_text(result.model_dump_json(indent=1), encoding="utf-8")
    pd.DataFrame([d.model_dump() for d in result.deals]).to_parquet(paths["deals"], index=False)
    balance = pd.DataFrame([b.model_dump() for b in result.balance])
    balance.to_parquet(paths["equity"], index=False)
    return paths


# --- the real engines --------------------------------------------------------------------------


GRANULARITY = {TickModel.OHLC_M1: "m1_ohlc", TickModel.REAL_TICKS: "ticks"}


class BacktestExecutor:
    """Runs MQL5 strategies in the Strategy Tester and Python ones in the simulator."""

    def __init__(self, settings: Callable[[], Any]) -> None:
        self.settings = settings

    def execute(self, ctx: JobContext) -> JobOutput:
        from strategylab.backtest import Hooks

        hooks = Hooks(on_stage=ctx.stage, on_log=ctx.log, cancel=ctx.cancel)
        if ctx.strategy.engine is Engine.MT5_TESTER:
            return self._mql5(ctx, hooks)
        return self._python(ctx, hooks)

    def _mql5(self, ctx: JobContext, hooks: Any) -> JobOutput:
        from strategylab.backtest import CompileFailedError, RunCancelledError, run_staged_mql5
        from strategylab.compiler import CompileError, load_staged
        from strategylab.params import ParamsError
        from strategylab.tester import DEFAULT_TIMEOUT_S, Outcome, SpecError

        settings = self.settings()
        s = ctx.run.settings
        try:
            staged = load_staged(Path(ctx.strategy.folder))
            run = run_staged_mql5(
                staged,
                settings,
                symbol=s.symbol,
                timeframe=s.timeframe,
                date_from=s.date_from,
                date_to=s.date_to,
                model=s.model,
                deposit=s.deposit,
                currency=s.currency,
                leverage=s.leverage,
                parameters=s.parameters,
                timeout_s=s.timeout_s or DEFAULT_TIMEOUT_S,
                run_id=ctx.run.id,
                hooks=hooks,
            )
        except CompileFailedError as exc:
            errors = "; ".join(str(d) for d in exc.result.errors[:5])
            return JobOutput(RunStatus.FAILED, "compile_failed", f"{exc} {errors}".strip())
        except (ParamsError, SpecError, CompileError) as exc:
            return JobOutput(RunStatus.FAILED, "invalid_settings", str(exc))
        except RunCancelledError:
            return JobOutput(RunStatus.CANCELLED, "cancelled", "Cancelled before the tester ran.")
        tester = run.tester
        artefacts = {f"log_{kind}": path for kind, path in tester.log_paths.items()}
        if tester.config_path is not None:
            artefacts["config"] = tester.config_path
        if tester.report_path is not None:
            artefacts["report"] = tester.report_path
        if run.result_path is not None:
            artefacts["result"] = run.result_path
        status = {
            Outcome.SUCCESS: RunStatus.DONE,
            Outcome.ZERO_TRADES: RunStatus.DONE,
            Outcome.CANCELLED: RunStatus.CANCELLED,
        }.get(tester.outcome, RunStatus.FAILED)
        return JobOutput(
            status,
            str(tester.outcome),
            tester.message,
            result=run.result if status is RunStatus.DONE else None,
            artefacts=artefacts,
        )

    def _python(self, ctx: JobContext, hooks: Any) -> JobOutput:
        from strategylab.backtest import RunCancelledError, run_python_strategy
        from strategylab.mt5_data import MT5DataError
        from strategylab.pystrategy import ScriptError, load_script, run_inputs
        from strategylab.sim.runner import DEFAULT_TIMEOUT_S, SimOutcome

        settings = self.settings()
        s = ctx.run.settings
        ctx.stage("compiling")
        try:
            script = load_script(Path(ctx.strategy.folder))
        except (OSError, ValueError) as exc:
            return JobOutput(RunStatus.FAILED, "invalid_settings", f"The script is missing: {exc}")
        errors = [d for d in script.diagnostics if d.severity == "error"]
        for diagnostic in script.diagnostics:
            ctx.log("compile", str(diagnostic))
        if errors:
            return JobOutput(RunStatus.FAILED, "compile_failed", str(errors[0]))
        try:
            constants, args = run_inputs(script.inputs, s.parameters)
        except ScriptError as exc:
            return JobOutput(RunStatus.FAILED, "invalid_settings", str(exc))
        granularity = GRANULARITY.get(s.model)
        if granularity is None:
            return JobOutput(
                RunStatus.FAILED,
                "invalid_settings",
                "Python strategies run on 1-minute bars or recorded ticks.",
            )
        try:
            sim = run_python_strategy(
                script.entry_path,
                settings,
                symbol=s.symbol,
                timeframe=s.timeframe,
                date_from=s.date_from,
                date_to=s.date_to,
                deposit=s.deposit,
                currency=s.currency,
                leverage=s.leverage,
                commission_per_lot=s.commission_per_lot,
                granularity=granularity,  # type: ignore[arg-type]
                timeout_s=s.timeout_s or DEFAULT_TIMEOUT_S,
                args=tuple(args),
                constants=constants,
                run_id=ctx.run.id,
                hooks=hooks,
            )
        except RunCancelledError:
            return JobOutput(RunStatus.CANCELLED, "cancelled", "Cancelled before the strategy ran.")
        except MT5DataError as exc:
            return JobOutput(RunStatus.FAILED, "no_history", str(exc))
        artefacts: dict[str, Path] = {"log_strategy": sim.log_path}
        if sim.result_path is not None:
            artefacts["result"] = sim.result_path
        status = {
            SimOutcome.SUCCESS: RunStatus.DONE,
            SimOutcome.ZERO_TRADES: RunStatus.DONE,
            SimOutcome.CANCELLED: RunStatus.CANCELLED,
        }.get(sim.outcome, RunStatus.FAILED)
        return JobOutput(
            status,
            str(sim.outcome),
            sim.message,
            result=sim.result if status is RunStatus.DONE else None,
            artefacts=artefacts,
        )

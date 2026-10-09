"""Backtests end to end: an MQL5 strategy in the Strategy Tester, a Python one in the simulator."""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from strategylab.compiler import CompileResult, StagedStrategy, compile_strategy, stage_upload
from strategylab.config import Settings, TerminalRunningError, ensure_terminal_idle
from strategylab.metrics import compute_metrics
from strategylab.mt5_data import MT5DataError, ensure_server_clock, export_history
from strategylab.params import (
    ParamsError,
    SetFile,
    build_set,
    discover,
    read_set,
    validate_value,
)
from strategylab.report import to_result
from strategylab.result import BacktestResult, TickModel
from strategylab.server_clock import ClockError, ServerClock
from strategylab.sessions import SessionError, export_sessions
from strategylab.sim.runner import DEFAULT_TIMEOUT_S as PYTHON_TIMEOUT_S
from strategylab.sim.runner import Granularity, PythonRunSpec, SimRun, run_python_backtest
from strategylab.tester import (
    DEFAULT_TIMEOUT_S,
    FIDELITY,
    BacktestSpec,
    TesterRun,
    new_run_id,
    run_tester,
)

ClockProvider = Callable[[Settings, date, date], ServerClock]


class CompileFailedError(Exception):
    def __init__(self, result: CompileResult) -> None:
        self.result = result
        super().__init__(f"{result.strategy.entry} did not compile ({len(result.errors)} errors).")


class RunCancelledError(Exception):
    """The run was cancelled before the engine started."""


@dataclass
class Hooks:
    """How a caller follows and stops a run. Every part is optional."""

    on_stage: Callable[[str], None] | None = None
    """Called with compiling, running or parsing as the run reaches each."""
    on_log: Callable[[str, str], None] | None = None
    """Called with (source, line) for compile messages, journals and strategy output."""
    cancel: threading.Event | None = None

    def stage(self, name: str) -> None:
        if self.on_stage is not None:
            self.on_stage(name)

    def log(self, source: str, line: str) -> None:
        if self.on_log is not None:
            self.on_log(source, line)

    def check(self) -> None:
        if self.cancel is not None and self.cancel.is_set():
            raise RunCancelledError


def measure_clock(settings: Settings, start: date, end: date) -> ServerClock:
    return ensure_server_clock(
        settings.terminal,
        settings.workspace_dir / "cache" / "server_clock",
        settings.account_server,
        start,
        end,
    )


def prepare_parameters(
    staged: StagedStrategy, overrides: Mapping[str, str]
) -> tuple[SetFile | None, dict[str, str], list[str]]:
    """The .set file for the tester, the overrides in the tester's form, and notes for the user.

    With source, every override is checked against the declared inputs and a complete .set is
    written. A prebuilt .ex5 shipped with a .set file uses that file with the overrides applied.
    A bare .ex5 can only pass overrides through as written.
    """
    set_files = sorted(name for name in staged.files if name.lower().endswith(".set"))
    shipped = staged.folder / set_files[0] if staged.prebuilt and set_files else None
    discovery = discover(staged.entry_path, staged.folder, shipped)
    notes = [discovery.note] if discovery.note else []
    if discovery.source == "source":
        by_name = {param.name: param for param in discovery.params}
        unknown = sorted(set(overrides) - set(by_name))
        if unknown:
            raise ParamsError(
                f"{Path(staged.entry).name} has no inputs named {', '.join(unknown)}. "
                f"Its inputs are: {', '.join(by_name) or 'none'}."
            )
        values = {name: validate_value(by_name[name], v) for name, v in overrides.items()}
        title = f"input parameters for {Path(staged.entry).stem}"
        return build_set(discovery.params, values, title=title), values, notes
    if shipped is not None:
        set_file = read_set(shipped)
        for name, value in overrides.items():
            set_file.set_value(name, value)
        return set_file, dict(overrides), notes
    return None, dict(overrides), notes


@dataclass
class BacktestRun:
    compile: CompileResult
    tester: TesterRun
    result: BacktestResult | None = None
    result_path: Path | None = None
    notes: list[str] = field(default_factory=list)


def run_mql5_backtest(upload: Path, settings: Settings, **options: Any) -> BacktestRun:
    """Stage an uploaded .mq5, .ex5 or .zip, then run it (see run_staged_mql5)."""
    staged = stage_upload(upload, settings.terminal.strategies_dir)
    return run_staged_mql5(staged, settings, **options)


def run_staged_mql5(
    staged: StagedStrategy,
    settings: Settings,
    *,
    symbol: str,
    timeframe: str,
    date_from: date,
    date_to: date,
    model: TickModel = TickModel.OHLC_M1,
    deposit: float = 10_000.0,
    currency: str = "USD",
    leverage: int = 100,
    parameters: Mapping[str, str] | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    clock_provider: ClockProvider = measure_clock,
    run_id: str | None = None,
    hooks: Hooks | None = None,
) -> BacktestRun:
    hooks = hooks or Hooks()
    install = settings.terminal
    hooks.stage("compiling")
    compiled = compile_strategy(staged, install)
    for diagnostic in compiled.diagnostics:
        hooks.log("compile", str(diagnostic))
    if not compiled.ok:
        raise CompileFailedError(compiled)

    parameter_file, values, parameter_notes = prepare_parameters(
        compiled.strategy, dict(parameters or {})
    )
    spec = BacktestSpec(
        expert=compiled.strategy.expert_path(install),
        symbol=symbol,
        timeframe=timeframe,
        date_from=date_from,
        date_to=date_to,
        model=model,
        deposit=deposit,
        currency=currency,
        leverage=leverage,
        parameters=values,
    )
    spec.validate()
    run_id = run_id or new_run_id()
    run_dir = settings.workspace_dir / "runs" / run_id
    notes: list[str] = list(parameter_notes)

    hooks.check()
    hooks.stage("running")
    clock: ServerClock | None = None
    try:
        # Measuring the clock attaches to the terminal; skip it when the tester will refuse anyway.
        ensure_terminal_idle(install)
        clock = clock_provider(settings, date_from, date_to)
    except TerminalRunningError:
        pass
    except (ClockError, MT5DataError) as exc:
        notes.append(f"Deal times are server time only: {exc}")

    hooks.check()
    tester = run_tester(
        spec,
        install,
        run_dir,
        run_id=run_id,
        timeout_s=timeout_s,
        parameter_file=parameter_file,
        cancel=hooks.cancel,
        on_log=hooks.log,
        on_parsing=lambda: hooks.stage("parsing"),
    )
    run = BacktestRun(compile=compiled, tester=tester, notes=notes)
    if tester.parsed is None:
        return run

    result = to_result(
        tester.parsed,
        model=model,
        fidelity=FIDELITY[model],
        strategy_hash=compiled.strategy.hash,
        clock=clock,
        expected={
            "strategy_name": Path(compiled.strategy.entry).stem,
            "symbol": symbol,
            "timeframe": timeframe,
            "date_from": date_from,
            "date_to": date_to,
            "deposit": deposit,
            "currency": currency.upper(),
            "leverage": leverage,
        },
    )
    result.meta.notes.extend(notes)
    result.metrics = compute_metrics(result.deals)
    run.result = result
    run.result_path = run_dir / "result.json"
    run.result_path.write_text(result.model_dump_json(indent=1), encoding="utf-8")
    return run


def run_python_strategy(
    script: Path,
    settings: Settings,
    *,
    symbol: str,
    timeframe: str,
    date_from: date,
    date_to: date,
    deposit: float = 10_000.0,
    currency: str = "USD",
    leverage: int = 100,
    commission_per_lot: float = 0.0,
    granularity: Granularity = "m1_ohlc",
    timeout_s: float = PYTHON_TIMEOUT_S,
    args: tuple[str, ...] = (),
    constants: dict[str, Any] | None = None,
    run_id: str | None = None,
    hooks: Hooks | None = None,
) -> SimRun:
    """Export the history a Python strategy needs, then run it in the simulator."""
    hooks = hooks or Hooks()
    hooks.stage("running")
    cache = settings.workspace_dir / "cache"
    hooks.log("stage", f"Exporting {symbol} history from the terminal.")
    bundle = export_history(
        settings.terminal,
        cache,
        symbol,
        timeframe,
        date_from,
        date_to,
        with_ticks=granularity == "ticks",
    )
    hooks.check()
    clock: ServerClock | None = None
    notes: list[str] = []
    try:
        clock = measure_clock(settings, date_from, date_to)
    except (ClockError, MT5DataError) as exc:
        notes.append(f"Deal times are server time only: {exc}")
    sessions: Path | None = None
    try:
        sessions = export_sessions(
            settings.terminal, symbol, bundle.m1.parent, settings.workspace_dir / "runs"
        )
    except SessionError as exc:
        notes.append(str(exc))
    spec = PythonRunSpec(
        script=script,
        symbol=symbol,
        timeframe=timeframe,
        date_from=date_from,
        date_to=date_to,
        deposit=deposit,
        currency=currency,
        leverage=leverage,
        commission_per_lot=commission_per_lot,
        granularity=granularity,
        args=tuple(args),
        constants=dict(constants or {}),
    )
    run_id = run_id or new_run_id()
    hooks.check()
    hooks.log("stage", "Starting the strategy in the simulator.")
    run = run_python_backtest(
        spec,
        m1=bundle.m1,
        spec_file=bundle.spec,
        history=bundle.history,
        ticks=bundle.ticks,
        sessions=sessions,
        run_dir=settings.workspace_dir / "runs" / run_id,
        run_id=run_id,
        server=bundle.server,
        clock=clock,
        timeout_s=timeout_s,
        cancel=hooks.cancel,
        on_log=lambda line: hooks.log("strategy", line),
        on_parsing=lambda: hooks.stage("parsing"),
    )
    if run.result is not None and notes:
        run.result.meta.notes.extend(notes)
        run.result_path.write_text(run.result.model_dump_json(indent=1), encoding="utf-8")
    return run

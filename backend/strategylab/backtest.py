"""One MQL5 backtest end to end: compile, find the server clock, run the tester, normalise."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from strategylab.compiler import CompileResult, compile_upload
from strategylab.config import Settings, TerminalRunningError, ensure_terminal_idle
from strategylab.mt5_data import MT5DataError, ensure_server_clock
from strategylab.report import to_result
from strategylab.result import BacktestResult, TickModel
from strategylab.server_clock import ClockError, ServerClock
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


def measure_clock(settings: Settings, start: date, end: date) -> ServerClock:
    return ensure_server_clock(
        settings.terminal,
        settings.workspace_dir / "cache" / "server_clock",
        settings.account_server,
        start,
        end,
    )


@dataclass
class BacktestRun:
    compile: CompileResult
    tester: TesterRun
    result: BacktestResult | None = None
    result_path: Path | None = None
    notes: list[str] = field(default_factory=list)


def run_mql5_backtest(
    upload: Path,
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
) -> BacktestRun:
    install = settings.terminal
    compiled = compile_upload(upload, install)
    if not compiled.ok:
        raise CompileFailedError(compiled)

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
        parameters=dict(parameters or {}),
    )
    spec.validate()
    run_id = new_run_id()
    run_dir = settings.workspace_dir / "runs" / run_id
    notes: list[str] = []

    clock: ServerClock | None = None
    try:
        # Measuring the clock attaches to the terminal; skip it when the tester will refuse anyway.
        ensure_terminal_idle(install)
        clock = clock_provider(settings, date_from, date_to)
    except TerminalRunningError:
        pass
    except (ClockError, MT5DataError) as exc:
        notes.append(f"Deal times are server time only: {exc}")

    tester = run_tester(spec, install, run_dir, run_id=run_id, timeout_s=timeout_s)
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
    run.result = result
    run.result_path = run_dir / "result.json"
    run.result_path.write_text(result.model_dump_json(indent=1), encoding="utf-8")
    return run

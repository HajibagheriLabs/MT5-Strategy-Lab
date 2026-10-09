"""Runs a Python strategy in a subprocess against the simulator.

The parent side exports history, starts `python -I -m strategylab.sim.runner <job>` with a wall
clock timeout, and turns what comes back into the normalised result. The child side builds the
simulation, puts the stand-in MetaTrader5 module and simulated time in place, and runs the
script unmodified. Strategy code never runs in the server process.

Inside the child, time is the server clock presented as UTC, which is how the MetaTrader5
package itself presents bar and tick times: time.time(), datetime.now(), datetime.utcnow() and
date.today() all return simulated server time, time.sleep() advances it, and the local time
zone is the server's. pandas.Timestamp.now(), threading and asyncio timers are not patched.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
import traceback
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from strategylab.result import (
    BacktestResult,
    Deal,
    Engine,
    Order,
    RunMeta,
    TickModel,
)
from strategylab.sim.constants import CONSTANTS

Granularity = Literal["m1_ohlc", "ticks"]
DEFAULT_TIMEOUT_S = 1800.0
# The conclusion of the parity study (reports/parity.md), shown with every Python run.
FIDELITY = {
    "m1_ohlc": (
        "Simulated by StrategyLab, not the Strategy Tester. Prices come from 1-minute bars, "
        "generated as in the tester's 1 minute OHLC mode; in the parity study the simulator "
        "reproduced that mode trade for trade. Like that mode it is "
        "optimistic against real ticks: the ask uses the narrowest spread of each minute, and "
        "stops and targets fill exactly at their level, even across a gap. In the study this "
        "overstated results by 1 to 12 points per trade. No commission unless one is set."
    ),
    "ticks": (
        "Simulated by StrategyLab, not the Strategy Tester, on the broker's recorded ticks with "
        "their own bid and ask. Stops and targets fill at the tick that crosses them, as the "
        "tester does with real ticks. A script that polls acts on the price when it wakes, "
        "which can be later than an EA reacting to the first tick of a bar. No commission "
        "unless one is set."
    ),
}
DEAL_TYPES = {
    CONSTANTS["DEAL_TYPE_BUY"]: "buy",
    CONSTANTS["DEAL_TYPE_SELL"]: "sell",
    CONSTANTS["DEAL_TYPE_BALANCE"]: "balance",
}
DEAL_ENTRIES = {
    CONSTANTS["DEAL_ENTRY_IN"]: "in",
    CONSTANTS["DEAL_ENTRY_OUT"]: "out",
    CONSTANTS["DEAL_ENTRY_INOUT"]: "in/out",
    CONSTANTS["DEAL_ENTRY_OUT_BY"]: "out by",
}
ORDER_TYPES = {
    CONSTANTS["ORDER_TYPE_BUY"]: "buy",
    CONSTANTS["ORDER_TYPE_SELL"]: "sell",
    CONSTANTS["ORDER_TYPE_BUY_LIMIT"]: "buy limit",
    CONSTANTS["ORDER_TYPE_SELL_LIMIT"]: "sell limit",
    CONSTANTS["ORDER_TYPE_BUY_STOP"]: "buy stop",
    CONSTANTS["ORDER_TYPE_SELL_STOP"]: "sell stop",
}
ORDER_STATES = {
    CONSTANTS["ORDER_STATE_FILLED"]: "filled",
    CONSTANTS["ORDER_STATE_CANCELED"]: "canceled",
    CONSTANTS["ORDER_STATE_EXPIRED"]: "expired",
    CONSTANTS["ORDER_STATE_REJECTED"]: "rejected",
    CONSTANTS["ORDER_STATE_PLACED"]: "placed",
}
TIMEFRAME_CODES = {
    name.removeprefix("TIMEFRAME_"): value
    for name, value in CONSTANTS.items()
    if name.startswith("TIMEFRAME_")
}


class SimOutcome(StrEnum):
    SUCCESS = "success"
    ZERO_TRADES = "zero_trades"
    ERROR = "error"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


# --- the child process -------------------------------------------------------------------------


def _install_time(clock: Any) -> None:
    import datetime as datetime_module
    import time as time_module

    real_gmtime = time_module.gmtime
    real_asctime = time_module.asctime
    real_strftime = time_module.strftime
    real_datetime = datetime_module.datetime
    real_date = datetime_module.date

    def seconds() -> float:
        return clock.now_ms / 1000

    def gmtime(secs: float | None = None) -> time_module.struct_time:
        return real_gmtime(seconds() if secs is None else secs)

    time_module.sleep = clock.sleep
    time_module.time = seconds
    time_module.time_ns = lambda: clock.now_ms * 1_000_000
    time_module.monotonic = seconds
    time_module.monotonic_ns = lambda: clock.now_ms * 1_000_000
    time_module.perf_counter = seconds
    time_module.perf_counter_ns = lambda: clock.now_ms * 1_000_000
    time_module.gmtime = gmtime
    time_module.localtime = gmtime
    time_module.asctime = lambda t=None: real_asctime(gmtime() if t is None else t)
    time_module.ctime = lambda secs=None: real_asctime(gmtime(secs))
    time_module.strftime = lambda fmt, t=None: real_strftime(fmt, gmtime() if t is None else t)

    class _SameAsReal(type):
        # Objects made by ordinary arithmetic are plain datetimes; they must still pass
        # isinstance checks against the patched names.
        def __instancecheck__(cls, obj: object) -> bool:
            return isinstance(obj, cls.__real__)

    class SimDateTime(real_datetime, metaclass=_SameAsReal):
        __real__ = real_datetime

        @classmethod
        def now(cls, tz: Any = None) -> Any:
            moment = real_datetime.fromtimestamp(seconds(), UTC)
            return moment.replace(tzinfo=None) if tz is None else moment.astimezone(tz)

        @classmethod
        def utcnow(cls) -> Any:
            return real_datetime.fromtimestamp(seconds(), UTC).replace(tzinfo=None)

        @classmethod
        def today(cls) -> Any:
            return cls.utcnow()

        @classmethod
        def fromtimestamp(cls, timestamp: float, tz: Any = None) -> Any:
            moment = real_datetime.fromtimestamp(timestamp, UTC)
            return moment.replace(tzinfo=None) if tz is None else moment.astimezone(tz)

    class SimDate(real_date, metaclass=_SameAsReal):
        __real__ = real_date

        @classmethod
        def today(cls) -> Any:
            return real_datetime.fromtimestamp(seconds(), UTC).date()

    datetime_module.datetime = SimDateTime
    datetime_module.date = SimDate


def _load_rates(path: str) -> Any:
    import numpy as np
    import pandas as pd

    from strategylab.sim.market import RATE_DTYPE

    frame = pd.read_parquet(path)
    rates = np.zeros(len(frame), dtype=RATE_DTYPE)
    for name in RATE_DTYPE.names:
        rates[name] = frame[name].to_numpy()
    return rates


def _load_ticks(path: str) -> Any:
    import numpy as np
    import pandas as pd

    from strategylab.sim.market import TICK_DTYPE

    frame = pd.read_parquet(path)
    ticks = np.zeros(len(frame), dtype=TICK_DTYPE)
    for name in TICK_DTYPE.names:
        ticks[name] = frame[name].to_numpy()
    return ticks


def build_simulation(job: dict[str, Any]) -> tuple[Any, Any, Any, Any]:
    """Market, broker, clock and terminal for a job description."""
    from strategylab.sim.broker import AccountSettings, Broker
    from strategylab.sim.clock import SimClock
    from strategylab.sim.market import Market, synthetic_stream, tick_stream
    from strategylab.sim.shim import TerminalStandIn

    spec_file = json.loads(Path(job["spec"]).read_text(encoding="utf-8"))
    spec = spec_file["symbol"]
    m1 = _load_rates(job["m1"])
    if job["granularity"] == "ticks":
        stream = tick_stream(_load_ticks(job["ticks"]), m1)
    else:
        stream = synthetic_stream(m1, float(spec["point"]), int(spec["digits"]))
    native = {}
    if job.get("history"):
        native[TIMEFRAME_CODES[job["timeframe"]]] = _load_rates(job["history"])
    market = Market(spec["name"], m1, stream, native)
    account = AccountSettings(
        deposit=float(job["deposit"]),
        currency=job["currency"],
        leverage=int(job["leverage"]),
        margin_mode=int(
            spec_file.get("margin_mode", CONSTANTS["ACCOUNT_MARGIN_MODE_RETAIL_HEDGING"])
        ),
        commission_per_lot=float(job.get("commission_per_lot", 0.0)),
    )
    start_ms, end_ms = int(job["start_ms"]), int(job["end_ms"])
    trade_sessions = None
    if job.get("sessions"):
        from strategylab.sessions import Sessions

        sessions = Sessions.from_json(Path(job["sessions"]).read_text(encoding="utf-8"))
        trade_sessions = sessions.trade
    broker = Broker(market, spec, account, start_ms, trade_sessions=trade_sessions)
    clock = SimClock(
        event_times_ms=stream.time_ms,
        now_ms=start_ms,
        end_ms=end_ms,
        advance=lambda target: broker.advance(target, end_ms),
        finish=broker.finish,
    )
    terminal = TerminalStandIn(broker, clock, market, int(spec_file.get("terminal_build", 0)))
    return market, broker, clock, terminal


def _run_script(script: Path, constants: dict[str, Any]) -> None:
    """Run the script as __main__, with the given module-level constants replaced."""
    import runpy

    if not constants:
        runpy.run_path(str(script), run_name="__main__")
        return
    import ast
    import types

    from strategylab.pystrategy import apply_constants

    tree = ast.parse(script.read_bytes(), filename=str(script))
    apply_constants(tree, constants)
    code = compile(tree, str(script), "exec")
    module = types.ModuleType("__main__")
    module.__file__ = str(script)
    previous = sys.modules.get("__main__")
    sys.modules["__main__"] = module
    try:
        exec(code, module.__dict__)
    finally:
        if previous is not None:
            sys.modules["__main__"] = previous


def child_main(job_path: str) -> int:
    from strategylab.sim import shim
    from strategylab.sim.clock import SimulationFinished

    # The log is followed while the strategy runs, and strategies print whatever they like:
    # isolated mode ignores PYTHONIOENCODING, so the encoding is set here.
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError, ValueError):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace", line_buffering=True)

    job = json.loads(Path(job_path).read_text(encoding="utf-8"))
    _, broker, clock, terminal = build_simulation(job)
    shim.install(terminal)
    _install_time(clock)

    script = Path(job["script"])
    sys.argv = [str(script), *job.get("args", [])]
    sys.path.insert(0, str(script.parent))
    status, error = "finished", None
    started = time.process_time()
    try:
        _run_script(script, job.get("constants") or {})
        status = "returned"
    except SimulationFinished:
        status = "finished"
    except SystemExit as exc:
        if exc.code not in (None, 0):
            status, error = "error", f"The strategy exited with {exc.code!r}."
        else:
            status = "returned"
    except BaseException:
        status, error = "error", traceback.format_exc()
    if not clock.finished:
        # The strategy stopped before the history ran out; like an EA removed from a chart,
        # its positions are closed where it stopped.
        broker.finish()
    result = {
        "status": status,
        "error": error,
        "deals": [asdict(d) for d in broker.deals],
        "orders": [asdict(o) for o in broker.history_orders],
        "notes": broker.notes,
        "stopped_at_ms": clock.now_ms,
        "sleeps": clock.sleeps,
        "cpu_seconds": round(time.process_time() - started, 2),
    }
    Path(job["output"]).write_text(json.dumps(result), encoding="utf-8")
    return 0


# --- the parent process ------------------------------------------------------------------------


@dataclass(frozen=True)
class PythonRunSpec:
    script: Path
    symbol: str
    timeframe: str
    """The timeframe the strategy trades on: used to export longer history and for display."""
    date_from: date
    date_to: date
    """Exclusive, as for the Strategy Tester."""
    deposit: float = 10_000.0
    currency: str = "USD"
    leverage: int = 100
    commission_per_lot: float = 0.0
    granularity: Granularity = "m1_ohlc"
    warmup_days: int = 30
    args: tuple[str, ...] = ()
    """Command-line arguments the script receives in sys.argv after its own name."""
    constants: dict[str, Any] = field(default_factory=dict)
    """Module-level constants given other values for this run (see pystrategy)."""


@dataclass
class SimRun:
    run_id: str
    outcome: SimOutcome
    message: str
    run_dir: Path
    log_path: Path
    result: BacktestResult | None = None
    result_path: Path | None = None
    elapsed_s: float = 0.0
    stats: dict[str, Any] = field(default_factory=dict)


def _ms(day: date) -> int:
    return int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp() * 1000)


def _server_time(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, UTC).replace(tzinfo=None)


def to_result(
    spec: PythonRunSpec,
    raw: dict[str, Any],
    *,
    server: str | None,
    build: int | None,
    clock: Any | None,
) -> BacktestResult:
    from strategylab.metrics import compute_metrics
    from strategylab.report import rebuild_balance

    def utc(ms: int) -> datetime | None:
        return clock.to_utc(_server_time(ms)) if clock is not None else None

    deals = [
        Deal(
            ticket=d["ticket"],
            server_time=_server_time(d["time_ms"]),
            time=utc(d["time_ms"]),
            symbol=d["symbol"] or None,
            type=DEAL_TYPES.get(d["type"], str(d["type"])),
            entry=None if d["type"] == CONSTANTS["DEAL_TYPE_BALANCE"] else DEAL_ENTRIES[d["entry"]],
            volume=d["volume"] or None,
            price=d["price"] or None,
            order=d["order"] or None,
            commission=d["commission"],
            swap=d["swap"],
            profit=d["profit"],
            balance=d["balance"],
            comment=d["comment"],
        )
        for d in raw["deals"]
    ]
    orders = [
        Order(
            ticket=o["ticket"],
            open_server_time=_server_time(o["time_setup_ms"]),
            open_time=utc(o["time_setup_ms"]),
            symbol=spec.symbol,
            type=ORDER_TYPES.get(o["type"], str(o["type"])),
            volume_initial=o["volume_initial"],
            volume_filled=o["volume_initial"] - o["volume_current"],
            price=o["price_open"],
            sl=o["sl"] or None,
            tp=o["tp"] or None,
            done_server_time=_server_time(o["time_done_ms"]) if o["time_done_ms"] else None,
            done_time=utc(o["time_done_ms"]) if o["time_done_ms"] else None,
            state=ORDER_STATES.get(o["state"], str(o["state"])),
            comment=o["comment"],
        )
        for o in raw["orders"]
    ]
    balance, notes = rebuild_balance(deals)
    notes = list(raw.get("notes", [])) + notes
    if raw["status"] == "returned":
        stopped = _server_time(raw["stopped_at_ms"])
        notes.append(f"The strategy stopped by itself at {stopped:%Y.%m.%d %H:%M} server time.")
    offsets: list[int] = []
    if clock is None:
        notes.append("Server UTC offset unknown: deal times are broker server time only.")
    elif deals:
        offsets = clock.offsets_between(deals[0].server_time, deals[-1].server_time)
    meta = RunMeta(
        engine=Engine.PYTHON_SIM,
        fidelity=FIDELITY[spec.granularity],
        strategy_name=spec.script.stem,
        strategy_hash=hashlib.sha256(spec.script.read_bytes()).hexdigest()[:16],
        symbol=spec.symbol,
        timeframe=spec.timeframe,
        date_from=spec.date_from,
        date_to=spec.date_to,
        model=TickModel.OHLC_M1 if spec.granularity == "m1_ohlc" else TickModel.REAL_TICKS,
        deposit=spec.deposit,
        currency=spec.currency.upper(),
        leverage=spec.leverage,
        parameters={},
        server=server,
        terminal_build=build,
        server_utc_offsets_h=offsets,
        notes=notes,
    )
    result = BacktestResult(meta=meta, deals=deals, orders=orders, balance=balance)
    result.metrics = compute_metrics(deals)
    return result


class _LogFollower:
    """Hands each complete line the strategy prints to a callback, as it is printed."""

    def __init__(self, path: Path, sink: Callable[[str], None] | None) -> None:
        self.path, self.sink = path, sink
        self.offset = 0
        self.partial = b""

    def poll(self, final: bool = False) -> None:
        if self.sink is None:
            return
        try:
            with self.path.open("rb") as handle:
                handle.seek(self.offset)
                raw = handle.read()
        except OSError:
            return
        self.offset += len(raw)
        data = self.partial + raw
        *complete, self.partial = data.split(b"\n")
        if final and self.partial:
            complete.append(self.partial)
            self.partial = b""
        for line in complete:
            self.sink(line.decode("utf-8", "replace").rstrip("\r"))


def run_python_backtest(
    spec: PythonRunSpec,
    *,
    m1: Path,
    spec_file: Path,
    run_dir: Path,
    run_id: str,
    history: Path | None = None,
    ticks: Path | None = None,
    sessions: Path | None = None,
    server: str | None = None,
    clock: Any | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    python: str = sys.executable,
    cancel: threading.Event | None = None,
    on_log: Callable[[str], None] | None = None,
    on_parsing: Callable[[], None] | None = None,
) -> SimRun:
    """Run the script on already exported history and normalise what it did.

    Setting `cancel` stops the strategy's process; `on_log` receives each line it prints as it
    prints it; `on_parsing` is called before its output is turned into the result.
    """
    if spec.granularity == "ticks" and ticks is None:
        raise ValueError("Tick granularity needs exported ticks.")
    run_dir.mkdir(parents=True, exist_ok=True)
    output = run_dir / "sim_output.json"
    log_path = run_dir / "strategy.log"
    job = {
        "script": str(spec.script.resolve()),
        "m1": str(m1),
        "history": str(history) if history else None,
        "ticks": str(ticks) if ticks else None,
        "spec": str(spec_file),
        "sessions": str(sessions) if sessions else None,
        "timeframe": spec.timeframe,
        "granularity": spec.granularity,
        "deposit": spec.deposit,
        "currency": spec.currency.upper(),
        "leverage": spec.leverage,
        "commission_per_lot": spec.commission_per_lot,
        "start_ms": _ms(spec.date_from),
        "end_ms": _ms(spec.date_to),
        "output": str(output),
        "args": list(spec.args),
        "constants": spec.constants,
    }
    job_path = run_dir / "sim_job.json"
    job_path.write_text(json.dumps(job, indent=1), encoding="utf-8")
    environment = {key: value for key, value in os.environ.items() if not key.startswith("PYTHON")}
    started = time.monotonic()
    follower = _LogFollower(log_path, on_log)
    with log_path.open("wb") as log:
        process = subprocess.Popen(
            [python, "-I", "-m", "strategylab.sim.runner", str(job_path)],
            cwd=run_dir,
            stdout=log,
            stderr=subprocess.STDOUT,
            env=environment,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        from strategylab.winproc import kill_tree, wait_for

        _, stopped_by = wait_for(process, timeout_s, cancel, follower.poll)
        if stopped_by is not None:
            kill_tree(process.pid)
            follower.poll()
            if stopped_by == "cancelled":
                outcome, message = SimOutcome.CANCELLED, "Cancelled; the strategy was stopped."
            else:
                outcome = SimOutcome.TIMEOUT
                message = (
                    f"The strategy did not finish within {timeout_s:.0f} s and was stopped. A "
                    "loop that never calls time.sleep() keeps the simulated clock still."
                )
            return SimRun(
                run_id, outcome, message, run_dir, log_path, elapsed_s=time.monotonic() - started
            )
    follower.poll(final=True)
    elapsed = time.monotonic() - started
    if not output.is_file():
        tail = log_path.read_text(encoding="utf-8", errors="replace")[-2000:]
        return SimRun(
            run_id,
            SimOutcome.ERROR,
            f"The simulator stopped before writing a result (exit code {process.returncode}). "
            f"Last output:\n{tail}",
            run_dir,
            log_path,
            elapsed_s=elapsed,
        )
    if on_parsing is not None:
        on_parsing()
    raw = json.loads(output.read_text(encoding="utf-8"))
    stats = {k: raw[k] for k in ("status", "sleeps", "cpu_seconds", "stopped_at_ms")}
    build = None
    with contextlib.suppress(OSError, ValueError):
        build = json.loads(spec_file.read_text(encoding="utf-8")).get("terminal_build")
    result = to_result(spec, raw, server=server, build=build, clock=clock)
    result_path = run_dir / "result.json"
    result_path.write_text(result.model_dump_json(indent=1), encoding="utf-8")
    if raw["status"] == "error":
        return SimRun(
            run_id,
            SimOutcome.ERROR,
            f"The strategy raised an error:\n{raw['error']}",
            run_dir,
            log_path,
            result,
            result_path,
            elapsed,
            stats,
        )
    outcome = SimOutcome.SUCCESS if result.trade_count else SimOutcome.ZERO_TRADES
    message = (
        f"{result.trade_count} trades."
        if result.trade_count
        else "The strategy ran but made no trades."
    )
    return SimRun(run_id, outcome, message, run_dir, log_path, result, result_path, elapsed, stats)


if __name__ == "__main__":
    sys.exit(child_main(sys.argv[1]))

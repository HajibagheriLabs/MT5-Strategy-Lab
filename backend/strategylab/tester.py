"""Strategy Tester configuration, terminal launch, wait and artefact collection.

A backtest is one terminal start with a generated configuration file: the terminal logs in with
its saved account, runs the [Tester] section, writes the report and shuts itself down. Every key
written here, and every behaviour relied on below, was checked against the "Platform Start" help
page and real runs; see DECISIONS.md.
"""

from __future__ import annotations

import codecs
import re
import shutil
import subprocess
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import StrEnum
from pathlib import Path, PureWindowsPath
from typing import Protocol

from strategylab.config import (
    TerminalInstall,
    TerminalRunningError,
    decode_mt_text,
    ensure_terminal_idle,
)
from strategylab.params import SetFile
from strategylab.report import ParsedReport, ReportError, parse_report
from strategylab.result import TickModel
from strategylab.winproc import StopOutcome, stop_process, wait_for

TIMEFRAMES = (
    "M1", "M2", "M3", "M4", "M5", "M6", "M10", "M12", "M15", "M20", "M30",
    "H1", "H2", "H3", "H4", "H6", "H8", "H12", "D1", "W1", "MN1",
)  # fmt: skip
MODEL_CODES = {
    TickModel.EVERY_TICK: 0,
    TickModel.OHLC_M1: 1,
    TickModel.OPEN_PRICES: 2,
    TickModel.REAL_TICKS: 4,
}
FIDELITY = {
    TickModel.REAL_TICKS: (
        "MetaTrader 5 Strategy Tester on the broker's recorded ticks: the closest a backtest "
        "gets to how orders would have filled."
    ),
    TickModel.EVERY_TICK: (
        "MetaTrader 5 Strategy Tester with ticks generated from M1 bars: real bar extremes, "
        "synthetic path and spread between them."
    ),
    TickModel.OHLC_M1: (
        "MetaTrader 5 Strategy Tester on 1-minute OHLC: four prices per minute, so stops and "
        "targets inside a minute are resolved without the real tick order."
    ),
    TickModel.OPEN_PRICES: (
        "MetaTrader 5 Strategy Tester on bar open prices only: valid only for strategies that act "
        "once per bar and use no intrabar stops."
    ),
}
DEFAULT_TIMEOUT_S = 1800.0
STOP_GRACE_S = 30.0
REPORTS_SUBDIR = PureWindowsPath("StrategyLab", "reports")
PARAMETER_FILE_PREFIX = "strategylab-"
# A launch that the already running terminal swallows comes back almost at once.
HANDOFF_SECONDS = 5.0

_SHUTDOWN = re.compile(r"shutdown with (?P<code>-?\d+)(?: \((?P<reason>[^)]*)\))?")
_PROBLEM = re.compile(r"^\S+\t[23]\t\S+\t(?:Tester|Terminal)\t(?P<message>.+)$", re.M)
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class Outcome(StrEnum):
    SUCCESS = "success"
    ZERO_TRADES = "zero_trades"
    NO_REPORT = "no_report"
    TIMEOUT = "timeout"
    TERMINAL_RUNNING = "terminal_running"
    CANCELLED = "cancelled"


class SpecError(ValueError):
    pass


@dataclass(frozen=True)
class BacktestSpec:
    expert: str
    """Path of the .ex5 relative to MQL5\\Experts, e.g. StrategyLab\\<hash>\\My EA.ex5."""
    symbol: str
    timeframe: str
    date_from: date
    date_to: date
    """Exclusive, as in the Strategy Tester: the test stops at 00:00 server time on this date."""
    model: TickModel = TickModel.OHLC_M1
    deposit: float = 10_000.0
    currency: str = "USD"
    leverage: int = 100
    parameters: Mapping[str, str] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.expert.strip():
            raise SpecError("No expert given.")
        if not self.symbol or any(ch in self.symbol for ch in "=\r\n;"):
            raise SpecError(f"'{self.symbol}' is not a symbol name.")
        if self.timeframe not in TIMEFRAMES:
            raise SpecError(
                f"'{self.timeframe}' is not a tester timeframe. Use one of {', '.join(TIMEFRAMES)}."
            )
        if self.date_to <= self.date_from:
            raise SpecError("The end date must be after the start date (the end is exclusive).")
        if self.deposit <= 0:
            raise SpecError("The deposit must be positive.")
        if not (len(self.currency) == 3 and self.currency.isalpha()):
            raise SpecError(f"'{self.currency}' is not a three-letter currency code.")
        if self.leverage < 1:
            raise SpecError("Leverage must be at least 1 (meaning 1:1).")
        for name, value in self.parameters.items():
            if not _NAME.match(name):
                raise SpecError(f"'{name}' is not an MQL5 input name.")
            if any(ch in str(value) for ch in "\r\n"):
                raise SpecError(f"The value of '{name}' contains a line break.")


def expert_setting(expert: str) -> str:
    """The Expert value as documented: relative to MQL5\\Experts, without the extension."""
    path = PureWindowsPath(expert.replace("/", "\\"))
    return str(path.with_suffix("")) if path.suffix.lower() == ".ex5" else str(path)


def _number(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


def build_config(spec: BacktestSpec, report: PureWindowsPath, parameters_file: str) -> str:
    """The configuration file for one backtest.

    [Experts] switches every chart EA off and forbids live trading: the tester runs without
    either, and nothing else should ever run in this terminal while StrategyLab drives it.
    """
    lines = [
        "[Experts]",
        "AllowLiveTrading=0",
        "AllowDllImport=0",
        "Enabled=0",
        "",
        "[Tester]",
        f"Expert={expert_setting(spec.expert)}",
        f"ExpertParameters={parameters_file}",
        f"Symbol={spec.symbol}",
        f"Period={spec.timeframe}",
        f"Model={MODEL_CODES[spec.model]}",
        "ExecutionMode=0",
        "Optimization=0",
        "ForwardMode=0",
        f"FromDate={spec.date_from:%Y.%m.%d}",
        f"ToDate={spec.date_to:%Y.%m.%d}",
        f"Deposit={_number(spec.deposit)}",
        f"Currency={spec.currency.upper()}",
        f"Leverage=1:{spec.leverage}",
        f"Report={report}",
        "ReplaceReport=1",
        "ShutdownTerminal=1",
        "Visual=0",
        "UseLocal=1",
        "UseRemote=0",
        "UseCloud=0",
    ]
    return "\r\n".join(lines) + "\r\n"


def parameters_text(parameters: Mapping[str, str]) -> str:
    return "".join(f"{name}={value}\r\n" for name, value in parameters.items())


def write_utf16(path: Path, text: str) -> None:
    """MetaTrader's own ini and .set files are UTF-16LE with a BOM; write ours the same way."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(codecs.BOM_UTF16_LE + text.encode("utf-16-le"))


def launch_command(install: TerminalInstall, config: Path) -> str:
    # Quoted after the colon, as for MetaEditor; see compiler.build_command.
    portable = " /portable" if install.portable else ""
    return f'"{install.terminal_exe}"{portable} /config:"{config}"'


class Process(Protocol):
    pid: int

    def wait(self, timeout: float | None = None) -> int: ...


Launcher = Callable[[str], Process]
Stopper = Callable[[int, float], StopOutcome]
LogSink = Callable[[str, str], None]
"""Receives (journal kind, line) as the run writes them."""


def launch_terminal(command: str) -> Process:
    startup = None
    if hasattr(subprocess, "STARTUPINFO"):
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 7  # SW_SHOWMINNOACTIVE: run minimised without taking focus
    return subprocess.Popen(command, startupinfo=startup)


LOG_KINDS = {
    "terminal": ("logs", "*.log"),
    "tester": ("Tester/logs", "*.log"),
    "agent": ("Tester", "Agent-*/logs/*.log"),
}


@dataclass
class LogCursor:
    """Remembers how long each journal was, so only lines written by this run are collected."""

    data_dir: Path
    sizes: dict[Path, int] = field(default_factory=dict)
    _read_to: dict[Path, int] = field(default_factory=dict)
    _partial: dict[Path, str] = field(default_factory=dict)

    def _files(self) -> dict[str, list[Path]]:
        found: dict[str, list[Path]] = {}
        for kind, (folder, pattern) in LOG_KINDS.items():
            base = self.data_dir / folder
            found[kind] = sorted(base.glob(pattern)) if base.is_dir() else []
        return found

    @classmethod
    def start(cls, data_dir: Path) -> LogCursor:
        cursor = cls(data_dir)
        for files in cursor._files().values():
            for path in files:
                cursor.sizes[path] = path.stat().st_size
        return cursor

    def collect(self) -> dict[str, str]:
        collected: dict[str, str] = {}
        for kind, files in self._files().items():
            parts = []
            for path in files:
                offset = self.sizes.get(path, 0)
                with path.open("rb") as handle:
                    handle.seek(offset - offset % 2)
                    raw = handle.read()
                if not raw:
                    continue
                text = raw.decode("utf-16-le", "replace") if offset else decode_mt_text(raw)
                parts.append(text.lstrip("\ufeff"))
            collected[kind] = "".join(parts)
        return collected

    def poll(self) -> list[tuple[str, str]]:
        """Complete lines written since the last poll, as (kind, line), for following a run live.

        The terminal keeps its journals open while it runs; a file it holds without sharing is
        skipped until the next poll.
        """
        lines: list[tuple[str, str]] = []
        for kind, files in self._files().items():
            for path in files:
                start = self._read_to.get(path, self.sizes.get(path, 0))
                try:
                    with path.open("rb") as handle:
                        handle.seek(start)
                        raw = handle.read()
                except OSError:
                    continue
                raw = raw[: len(raw) - len(raw) % 2]
                if not raw:
                    continue
                self._read_to[path] = start + len(raw)
                text = raw.decode("utf-16-le", "replace").lstrip("\ufeff")
                text = self._partial.pop(path, "") + text
                *complete, rest = text.split("\n")
                if rest:
                    self._partial[path] = rest
                lines.extend((kind, line.rstrip("\r")) for line in complete if line.strip())
        return lines


def signed_exit_code(code: int | None) -> int | None:
    """Windows exit codes arrive unsigned; the terminal's own journal prints them signed."""
    if code is None:
        return None
    return code - (1 << 32) if code >= (1 << 31) else code


def explain_failure(journals: Mapping[str, str]) -> str | None:
    """The terminal's own words for why a run ended badly, from its journal."""
    terminal = journals.get("terminal", "")
    problems = [m["message"].strip() for m in _PROBLEM.finditer(terminal)]
    shutdown = None
    for match in _SHUTDOWN.finditer(terminal):
        shutdown = match
    parts = [p for p in problems if p != "tester didn't start"]
    if shutdown and shutdown["reason"]:
        parts.append(shutdown["reason"])
    unique = list(dict.fromkeys(parts))
    return "; ".join(unique) if unique else None


@dataclass
class TesterRun:
    run_id: str
    outcome: Outcome
    message: str
    run_dir: Path
    config_path: Path | None = None
    exit_code: int | None = None
    report_path: Path | None = None
    parsed: ParsedReport | None = None
    log_paths: dict[str, Path] = field(default_factory=dict)
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    elapsed_s: float = 0.0
    stop: StopOutcome | None = None


def new_run_id() -> str:
    return f"{datetime.now(UTC):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"


def run_tester(
    spec: BacktestSpec,
    install: TerminalInstall,
    run_dir: Path,
    *,
    run_id: str | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    launcher: Launcher = launch_terminal,
    stopper: Stopper = stop_process,
    parameter_file: SetFile | None = None,
    cancel: threading.Event | None = None,
    on_log: LogSink | None = None,
    on_parsing: Callable[[], None] | None = None,
) -> TesterRun:
    """Run one backtest. Setting `cancel` closes the terminal (killing it if it will not close)
    and ends the run as cancelled; `on_log` receives journal lines while the terminal runs;
    `on_parsing` is called before the report is read."""
    spec.validate()
    run_id = run_id or new_run_id()
    run_dir.mkdir(parents=True, exist_ok=True)
    run = TesterRun(run_id=run_id, outcome=Outcome.NO_REPORT, message="", run_dir=run_dir)

    try:
        ensure_terminal_idle(install)
    except TerminalRunningError as exc:
        run.outcome, run.message = Outcome.TERMINAL_RUNNING, str(exc)
        return run

    # The tester only writes reports below the terminal folder and only reads parameter files
    # from MQL5\Profiles\Tester; both are moved out or removed when the run ends.
    report_rel = REPORTS_SUBDIR / run_id / "report"
    report_dir = install.data_dir / REPORTS_SUBDIR / run_id
    report_dir.mkdir(parents=True, exist_ok=True)
    parameters_name = f"{PARAMETER_FILE_PREFIX}{run_id}.set"
    parameters_path = install.mql5_dir / "Profiles" / "Tester" / parameters_name
    # Always pass a parameter file, even an empty one: without it the tester falls back to
    # whatever was last used for an EA of the same name.
    if parameter_file is not None:
        parameters_path.parent.mkdir(parents=True, exist_ok=True)
        parameters_path.write_bytes(parameter_file.to_bytes())
    else:
        write_utf16(parameters_path, parameters_text(spec.parameters))
    config_path = run_dir / "tester.ini"
    write_utf16(config_path, build_config(spec, report_rel, parameters_name))
    run.config_path = config_path

    cursor = LogCursor.start(install.data_dir)
    follower = LogCursor.start(install.data_dir)

    def follow() -> None:
        if on_log is not None:
            for kind, line in follower.poll():
                on_log(kind, line)

    started = time.monotonic()
    try:
        process = launcher(launch_command(install, config_path))
        code, stopped_by = wait_for(process, timeout_s, cancel, follow)
        if stopped_by is None:
            run.exit_code = signed_exit_code(code)
        else:
            run.stop = stopper(process.pid, STOP_GRACE_S)
            how = "closed" if run.stop != "killed" else "killed after not closing"
            if stopped_by == "cancelled":
                run.outcome = Outcome.CANCELLED
                run.message = f"Cancelled; the terminal was {how}."
            else:
                run.outcome = Outcome.TIMEOUT
                run.message = (
                    f"The tester did not finish within {timeout_s:.0f} s; the terminal was "
                    f"{how}. Use a shorter range or a faster tick model, or raise the timeout."
                )
        follow()
    finally:
        run.elapsed_s = time.monotonic() - started
        journals = cursor.collect()
        logs_dir = run_dir / "logs"
        logs_dir.mkdir(exist_ok=True)
        for kind, text in journals.items():
            path = logs_dir / f"{kind}.log"
            # Journals already end lines with CRLF; writing bytes keeps them from doubling.
            path.write_bytes(text.encode("utf-8"))
            run.log_paths[kind] = path
        parameters_path.unlink(missing_ok=True)

    report = report_dir / "report.htm"
    if report.is_file():
        target = run_dir / "report"
        target.mkdir(exist_ok=True)
        for item in report_dir.iterdir():
            shutil.move(str(item), target / item.name)
        run.report_path = target / "report.htm"
    shutil.rmtree(report_dir, ignore_errors=True)

    if run.outcome in (Outcome.TIMEOUT, Outcome.CANCELLED):
        return run

    launched = str(config_path).lower() in journals.get("terminal", "").lower()
    if not launched and run.elapsed_s < HANDOFF_SECONDS and run.report_path is None:
        run.outcome = Outcome.TERMINAL_RUNNING
        run.message = (
            "The terminal exited at once without reading the configuration, which is what it does "
            "when another copy is already running from the same folder. Close it and try again."
        )
        return run

    if run.report_path is None:
        reason = explain_failure(journals)
        run.outcome = Outcome.NO_REPORT
        run.message = (
            f"The tester produced no report ({reason})."
            if reason
            else "The tester produced no report and the terminal journal does not say why; see "
            "the logs collected for this run."
        )
        return run

    if on_parsing is not None:
        on_parsing()
    try:
        run.parsed = parse_report(run.report_path)
    except ReportError as exc:
        run.outcome = Outcome.NO_REPORT
        run.message = f"The tester wrote a report that could not be read: {exc}"
        return run

    if run.parsed.trade_count == 0:
        run.outcome = Outcome.ZERO_TRADES
        run.message = (
            "The test ran but the strategy made no trades. Check its inputs, the symbol and the "
            "date range."
        )
    else:
        run.outcome = Outcome.SUCCESS
        run.message = f"{run.parsed.trade_count} trades."
    return run

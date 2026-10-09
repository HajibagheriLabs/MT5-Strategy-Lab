"""A symbol's trading sessions, read from the terminal by a small expert run in the tester.

Brokers quote prices for longer than they accept orders: the demo server here quotes EURUSD from
00:00 but trades it only from 00:03 (00:05 on Monday) to 23:59, and the Strategy Tester rejects
orders outside those hours with "Market closed". The MetaTrader5 Python package cannot read
sessions, so SessionExport.mq5 prints them from inside the tester and they are cached per server
and symbol.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from strategylab.compiler import compile_upload
from strategylab.config import TerminalInstall
from strategylab.result import TickModel
from strategylab.tester import BacktestSpec, Outcome, run_tester

EXPORTER = Path(__file__).parent / "mql5" / "SessionExport.mq5"
_LINE = re.compile(r"SESSION (QUOTE|TRADE) (\d) (\d+) (\d+)")
# Any ordinary trading day works; the expert only prints the symbol's settings.
PROBE_DAY = date(2025, 6, 2)


class SessionError(Exception):
    pass


@dataclass(frozen=True)
class Sessions:
    """Session windows per MQL5 weekday (0 = Sunday), as (start, end) seconds after midnight."""

    trade: dict[int, list[tuple[int, int]]]
    quote: dict[int, list[tuple[int, int]]]

    def to_json(self) -> str:
        return json.dumps({"trade": self.trade, "quote": self.quote}, indent=1)

    @classmethod
    def from_json(cls, text: str) -> Sessions:
        data = json.loads(text)

        def windows(raw: dict[str, list[list[int]]]) -> dict[int, list[tuple[int, int]]]:
            return {int(day): [(int(a), int(b)) for a, b in spans] for day, spans in raw.items()}

        return cls(trade=windows(data["trade"]), quote=windows(data["quote"]))


def parse_session_log(text: str) -> Sessions:
    trade: dict[int, list[tuple[int, int]]] = {day: [] for day in range(7)}
    quote: dict[int, list[tuple[int, int]]] = {day: [] for day in range(7)}
    found = False
    for kind, day, start, end in _LINE.findall(text):
        found = True
        (trade if kind == "TRADE" else quote)[int(day)].append((int(start), int(end)))
    if not found:
        raise SessionError("The session exporter printed nothing; see the tester logs.")
    return Sessions(trade=trade, quote=quote)


def sessions_path(history_folder: Path) -> Path:
    return history_folder / "sessions.json"


def export_sessions(
    install: TerminalInstall, symbol: str, history_folder: Path, runs_dir: Path
) -> Path:
    """Read the symbol's sessions with a one-day tester run, unless they are already cached."""
    target = sessions_path(history_folder)
    if target.is_file():
        return target
    compiled = compile_upload(EXPORTER, install)
    if not compiled.ok:
        raise SessionError(f"The session exporter did not compile: {compiled.errors}")
    spec = BacktestSpec(
        expert=compiled.strategy.expert_path(install),
        symbol=symbol,
        timeframe="H1",
        date_from=PROBE_DAY,
        date_to=PROBE_DAY.replace(day=PROBE_DAY.day + 1),
        model=TickModel.OPEN_PRICES,
    )
    run = run_tester(spec, install, runs_dir / f"sessions-{target.parent.name}")
    if run.outcome not in (Outcome.SUCCESS, Outcome.ZERO_TRADES):
        raise SessionError(f"Reading the sessions of {symbol} failed: {run.message}")
    sessions = parse_session_log(run.log_paths["agent"].read_text(encoding="utf-8"))
    history_folder.mkdir(parents=True, exist_ok=True)
    target.write_text(sessions.to_json(), encoding="utf-8")
    return target

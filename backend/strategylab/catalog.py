"""Symbols with history in the terminal, and the dates that history covers.

The list comes from the terminal's history folders on disk (bases\\<server>\\history\\<symbol>,
one file of M1 bars per year), so reading it costs nothing. The first and last M1 bar of each
symbol are measured through a data session, which needs the terminal, and cached; a symbol is
measured again when its yearly files change (older history downloaded, a new year begun) or
after a day. Recorded ticks are listed by the months the terminal has stored
(bases\\<server>\\ticks\\<symbol>\\YYYYMM.tkc).

The terminal belongs to the job queue while a run is in progress, so measuring takes the queue's
terminal lock without waiting for it: when a run holds it, the last measurement is returned
with a note instead.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from strategylab.config import TerminalInstall

REMEASURE_AFTER = timedelta(days=1)

Measure = Callable[[TerminalInstall, dict[str, int]], tuple[str, list[dict[str, Any]]]]


def _default_measure(
    install: TerminalInstall, first_years: dict[str, int]
) -> tuple[str, list[dict[str, Any]]]:
    from strategylab.mt5_data import measure_history

    return measure_history(install, first_years)


@dataclass(frozen=True)
class SymbolHistory:
    name: str
    years: tuple[int, ...]
    """Years with an M1 history file on disk."""
    tick_months: tuple[str, ...] = ()
    """Months with recorded ticks on disk, as YYYY-MM."""
    description: str | None = None
    digits: int | None = None
    path: str | None = None
    first_bar: datetime | None = None
    """Server time of the first M1 bar."""
    last_bar: datetime | None = None
    measured_at: datetime | None = None

    @property
    def measured(self) -> bool:
        return self.first_bar is not None and self.last_bar is not None

    @property
    def bars_from(self) -> date | None:
        return self.first_bar.date() if self.first_bar else None

    @property
    def bars_to(self) -> date | None:
        return self.last_bar.date() if self.last_bar else None

    @property
    def ticks_from(self) -> date | None:
        if not self.tick_months:
            return None
        year, month = self.tick_months[0].split("-")
        return date(int(year), int(month), 1)

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        for key in ("first_bar", "last_bar", "measured_at"):
            data[key] = data[key].isoformat() if data[key] else None
        return data

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> SymbolHistory:
        values = dict(data)
        for key in ("first_bar", "last_bar", "measured_at"):
            values[key] = datetime.fromisoformat(values[key]) if values.get(key) else None
        values["years"] = tuple(values.get("years", ()))
        values["tick_months"] = tuple(values.get("tick_months", ()))
        return cls(**values)


@dataclass
class CatalogView:
    server: str | None
    symbols: list[SymbolHistory]
    pending: list[str] = field(default_factory=list)
    """Symbols whose dates could not be measured this time."""
    message: str | None = None

    def get(self, name: str) -> SymbolHistory | None:
        return next((s for s in self.symbols if s.name == name), None)


def _server_time(epoch: int | None) -> datetime | None:
    return datetime.fromtimestamp(epoch, UTC).replace(tzinfo=None) if epoch is not None else None


class HistoryCatalog:
    def __init__(
        self,
        install: TerminalInstall,
        server: str | None,
        cache_dir: Path,
        terminal_lock: threading.Lock,
        measure: Measure = _default_measure,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.install = install
        self.configured_server = server
        self.cache_dir = cache_dir
        self.terminal_lock = terminal_lock
        self.measure = measure
        self.clock = clock
        self._guard = threading.Lock()

    def server(self) -> str | None:
        servers = self.install.history_servers()
        if self.configured_server:
            return self.configured_server
        return servers[0] if len(servers) == 1 else None

    def _cache_path(self, server: str) -> Path:
        safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in server)
        return self.cache_dir / f"{safe}.json"

    def _on_disk(self, server: str) -> dict[str, SymbolHistory]:
        base = self.install.data_dir / "bases" / server
        history = base / "history"
        found: dict[str, SymbolHistory] = {}
        if not history.is_dir():
            return found
        for folder in sorted(p for p in history.iterdir() if p.is_dir()):
            years = tuple(sorted(int(f.stem) for f in folder.glob("*.hcc") if f.stem.isdigit()))
            if not years:
                continue
            ticks = base / "ticks" / folder.name
            months = tuple(
                sorted(
                    f"{f.stem[:4]}-{f.stem[4:]}"
                    for f in (ticks.glob("*.tkc") if ticks.is_dir() else ())
                    if len(f.stem) == 6 and f.stem.isdigit()
                )
            )
            found[folder.name] = SymbolHistory(name=folder.name, years=years, tick_months=months)
        return found

    def _load(self, server: str) -> dict[str, SymbolHistory]:
        path = self._cache_path(server)
        if not path.is_file():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        return {item["name"]: SymbolHistory.from_json(item) for item in data["symbols"]}

    def _save(self, server: str, symbols: dict[str, SymbolHistory]) -> None:
        path = self._cache_path(server)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"server": server, "symbols": [s.to_json() for s in symbols.values()]}
        path.write_text(json.dumps(payload, indent=1), encoding="utf-8")

    def _stale(self, disk: SymbolHistory, cached: SymbolHistory | None) -> bool:
        if cached is None or not cached.measured or cached.measured_at is None:
            return True
        if cached.years[:1] != disk.years[:1] or len(cached.years) != len(disk.years):
            return True
        return self.clock() - cached.measured_at > REMEASURE_AFTER

    def view(self, refresh: bool = False, only: list[str] | None = None) -> CatalogView:
        """Every symbol with history, measuring the stale ones if the terminal is free."""
        server = self.server()
        if server is None:
            servers = self.install.history_servers()
            message = (
                "The terminal has no history yet. Log it in to the demo account and open a chart."
                if not servers
                else f"The terminal holds history for several servers ({', '.join(servers)}); "
                "set [account] server in local.toml."
            )
            return CatalogView(None, [], message=message)
        with self._guard:
            disk = self._on_disk(server)
            cached = self._load(server)
            merged: dict[str, SymbolHistory] = {}
            stale: list[str] = []
            for name, found in disk.items():
                previous = cached.get(name)
                if previous is not None:
                    found = replace(previous, years=found.years, tick_months=found.tick_months)
                merged[name] = found
                wanted = only is None or name in only
                if wanted and (refresh or self._stale(found, previous)):
                    stale.append(name)
            message = None
            if stale:
                if self.terminal_lock.acquire(blocking=False):
                    try:
                        merged, message = self._measure(server, merged, stale)
                    finally:
                        self.terminal_lock.release()
                else:
                    message = (
                        "The terminal is busy with a run, so the dates of "
                        f"{', '.join(stale)} are from the last measurement, if any."
                    )
                if message is None:
                    stale = []
            self._save(server, merged)
            return CatalogView(server, list(merged.values()), stale, message)

    def _measure(
        self, server: str, merged: dict[str, SymbolHistory], names: list[str]
    ) -> tuple[dict[str, SymbolHistory], str | None]:
        try:
            _, found = self.measure(self.install, {n: merged[n].years[0] for n in names})
        except Exception as exc:  # the terminal can fail in many ways; report, keep the cache
            return merged, f"The history dates could not be measured: {exc}"
        stamp = self.clock()
        for item in found:
            previous = merged[item["name"]]
            merged[item["name"]] = SymbolHistory(
                name=previous.name,
                years=previous.years,
                tick_months=previous.tick_months,
                description=item.get("description"),
                digits=item.get("digits"),
                path=item.get("path"),
                first_bar=_server_time(item.get("first_bar")),
                last_bar=_server_time(item.get("last_bar")),
                measured_at=stamp,
            )
        return merged, None

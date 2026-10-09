"""Price bars for a run's chart, served in windows so the browser loads only what it shows.

A Python run already has the M1 bars it was simulated on; they are aggregated to the run's
timeframe. A tester run has no bars of its own, so they are exported once through a data
connection (which needs the terminal, so only when no run holds it) and cached. Times are server
time as seconds since 1970, like every bar in MetaTrader and like a deal's server time.
"""

from __future__ import annotations

import json
import threading
from collections import OrderedDict
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from strategylab.config import Settings
from strategylab.sim.constants import CONSTANTS
from strategylab.sim.market import bucket_start
from strategylab.store import RunRecord

EXPORTABLE = ("M1", "M5", "M15", "M30", "H1", "H4", "D1")
MINUTES = {
    **{f"M{m}": m for m in (1, 2, 3, 4, 5, 6, 10, 12, 15, 20, 30)},
    **{f"H{h}": h * 60 for h in (1, 2, 3, 4, 6, 8, 12)},
    "D1": 1440,
}
COLUMNS = ["time", "open", "high", "low", "close"]
MEMORY = 8


class BarsUnavailableError(Exception):
    """The bars cannot be produced now; the message says why and what to do."""


def source_timeframe(timeframe: str) -> str:
    """The exportable timeframe a chart timeframe is built from: the largest that divides it."""
    if timeframe in ("W1", "MN1"):
        return "D1"
    minutes = MINUTES[timeframe]
    for candidate in sorted(EXPORTABLE, key=lambda t: -MINUTES[t]):
        if minutes % MINUTES[candidate] == 0:
            return candidate
    return "M1"


def aggregate(frame: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Bars of `timeframe` from finer bars, grouped the way MetaTrader opens them."""
    if frame.empty:
        return frame[COLUMNS].copy()
    times = frame["time"].to_numpy(dtype=np.int64) * 1000
    buckets = bucket_start(times, CONSTANTS[f"TIMEFRAME_{timeframe}"]) // 1000
    grouped = frame.assign(bucket=buckets).groupby("bucket", sort=True)
    out = pd.DataFrame(
        {
            "time": grouped["time"].first().index.to_numpy(),
            "open": grouped["open"].first().to_numpy(),
            "high": grouped["high"].max().to_numpy(),
            "low": grouped["low"].min().to_numpy(),
            "close": grouped["close"].last().to_numpy(),
        }
    )
    return out


def _epoch(day: date) -> int:
    return int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp())


def _within(frame: pd.DataFrame, run: RunRecord) -> pd.DataFrame:
    start, end = _epoch(run.settings.date_from), _epoch(run.settings.date_to)
    return frame[(frame["time"] >= start) & (frame["time"] < end)].reset_index(drop=True)


Exporter = Callable[[Settings, str, str, date, date], pd.DataFrame]


def _export(
    settings: Settings, symbol: str, timeframe: str, start: date, end: date
) -> pd.DataFrame:
    from strategylab.mt5_data import open_session

    with open_session(settings.terminal) as session:
        return session.rates(symbol, timeframe, start, end)


class BarStore:
    def __init__(
        self,
        settings: Callable[[], Settings],
        terminal_lock: threading.Lock,
        exporter: Exporter = _export,
    ) -> None:
        self.settings = settings
        self.terminal_lock = terminal_lock
        self.exporter = exporter
        self._memory: OrderedDict[str, pd.DataFrame] = OrderedDict()
        self._guard = threading.Lock()

    def bars(self, run: RunRecord, run_dir: Path) -> pd.DataFrame:
        with self._guard:
            cached = self._memory.get(run.id)
            if cached is not None:
                self._memory.move_to_end(run.id)
                return cached
        frame = self._load(run, run_dir)
        with self._guard:
            self._memory[run.id] = frame
            while len(self._memory) > MEMORY:
                self._memory.popitem(last=False)
        return frame

    def _load(self, run: RunRecord, run_dir: Path) -> pd.DataFrame:
        timeframe = run.settings.timeframe
        job = run_dir / "sim_job.json"
        if job.is_file():
            m1 = Path(json.loads(job.read_text(encoding="utf-8"))["m1"])
            if m1.is_file():
                return _within(aggregate(pd.read_parquet(m1), timeframe), run)
        settings = self.settings()
        base = source_timeframe(timeframe)
        s = run.settings
        cache = (
            settings.workspace_dir
            / "cache"
            / "bars"
            / "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in s.symbol)
            / f"{base}_{s.date_from:%Y%m%d}_{s.date_to:%Y%m%d}.parquet"
        )
        if not cache.is_file():
            if not self.terminal_lock.acquire(blocking=False):
                raise BarsUnavailableError(
                    "The price chart needs the terminal, which is busy with a run. It will load "
                    "when the run finishes."
                )
            try:
                frame = self.exporter(settings, s.symbol, base, s.date_from, s.date_to)
            except Exception as exc:  # the terminal can fail in many ways; say which
                raise BarsUnavailableError(f"The price bars could not be read: {exc}") from exc
            finally:
                self.terminal_lock.release()
            cache.parent.mkdir(parents=True, exist_ok=True)
            frame[COLUMNS].to_parquet(cache, index=False)
        frame = pd.read_parquet(cache)
        return _within(aggregate(frame, timeframe) if base != timeframe else frame[COLUMNS], run)


def window(
    frame: pd.DataFrame,
    *,
    around: int | None = None,
    before: int | None = None,
    after: int | None = None,
    count: int = 500,
) -> tuple[pd.DataFrame, int]:
    """Up to `count` bars: centred on `around`, ending before `before`, or starting after
    `after`; the last `count` bars by default. Also returns the index of the first bar."""
    times = frame["time"].to_numpy()
    if around is not None:
        centre = int(np.searchsorted(times, around, side="right")) - 1
        start = max(0, min(centre - count // 2, len(frame) - count))
    elif before is not None:
        stop = int(np.searchsorted(times, before, side="left"))
        start = max(0, stop - count)
        return frame.iloc[start:stop], start
    elif after is not None:
        start = int(np.searchsorted(times, after, side="right"))
    else:
        start = max(0, len(frame) - count)
    start = max(0, start)
    return frame.iloc[start : start + count], start


def as_points(frame: pd.DataFrame) -> list[dict[str, Any]]:
    return [
        {"time": int(r.time), "open": r.open, "high": r.high, "low": r.low, "close": r.close}
        for r in frame.itertuples()
    ]

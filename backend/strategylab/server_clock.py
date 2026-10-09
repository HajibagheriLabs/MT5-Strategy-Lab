"""Broker server time to UTC, measured from the server's own price history.

MetaTrader stamps everything (bars, ticks, deals, tester reports) in the trade server's local
time, and brokers choose that clock freely: UTC+2 with European daylight saving, UTC+2 with US
daylight saving, plain UTC, and sometimes a change of rule along the way. Assuming any one rule
would silently shift timestamps by an hour for weeks at a time.

The forex week opens at 17:00 New York time on Sunday everywhere. So the first H1 bar after each
weekend gap of an FX symbol says exactly how far that server's clock was from UTC that week.
Daylight saving switches happen at weekends while FX is closed, so one offset per trading week is
exact for FX. Instruments that trade through the weekend could be an hour out between a switch
and the next weekly open.
"""

from __future__ import annotations

import json
import statistics
from bisect import bisect_right
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from functools import cached_property
from itertools import pairwise
from pathlib import Path
from zoneinfo import ZoneInfo

NEW_YORK = ZoneInfo("America/New_York")
FX_WEEK_OPEN_HOUR_NY = 17
WEEKEND_GAP = timedelta(hours=36)
SUNDAY, MONDAY = 6, 0


class ClockError(Exception):
    pass


@dataclass(frozen=True)
class WeekOffset:
    week_open: datetime
    """Server time of the first bar of the trading week (naive)."""
    offset_hours: int


def fx_week_open_utc(server_open: datetime) -> datetime:
    """17:00 New York on the Sunday that starts the trading week containing `server_open`."""
    day = server_open.date()
    while day.weekday() != SUNDAY:
        day -= timedelta(days=1)
    local = datetime(day.year, day.month, day.day, FX_WEEK_OPEN_HOUR_NY, tzinfo=NEW_YORK)
    return local.astimezone(UTC).replace(tzinfo=None)


def detect_week_offsets(bar_times: Sequence[datetime]) -> list[WeekOffset]:
    """Measure the server's UTC offset for every trading week covered by H1 bar open times.

    A weekly open is the first bar after a gap of at least 36 hours that falls on a Sunday or
    Monday in server time; mid-week holiday gaps (25 December, 1 January) are not weekly opens.
    A late open after a holiday (say Monday 06:00) still passes that test, so measurements more
    than an hour from the median are discarded: a real clock change moves by exactly an hour.
    """
    candidates: list[WeekOffset] = []
    for previous, current in pairwise(bar_times):
        if current - previous < WEEKEND_GAP or current.weekday() not in (SUNDAY, MONDAY):
            continue
        delta = current - fx_week_open_utc(current)
        hours, remainder = divmod(delta.total_seconds(), 3600)
        if remainder:
            continue
        candidates.append(WeekOffset(current, int(hours)))
    if not candidates:
        return []
    median = statistics.median(c.offset_hours for c in candidates)
    return [c for c in candidates if abs(c.offset_hours - median) <= 1]


@dataclass(frozen=True)
class ServerClock:
    server: str
    reference_symbol: str
    weeks: tuple[WeekOffset, ...]
    measured_at: datetime

    @classmethod
    def from_bars(
        cls, server: str, reference_symbol: str, bar_times: Sequence[datetime]
    ) -> ServerClock:
        weeks = detect_week_offsets(bar_times)
        if not weeks:
            raise ClockError(
                f"No weekly market opens were found in {reference_symbol} on {server}, so the "
                "server's UTC offset cannot be measured. It needs an FX symbol with H1 history."
            )
        return cls(server, reference_symbol, tuple(weeks), datetime.now(UTC))

    @property
    def first_week(self) -> datetime:
        return self.weeks[0].week_open

    @property
    def last_week(self) -> datetime:
        return self.weeks[-1].week_open

    @cached_property
    def _starts(self) -> list[datetime]:
        return [week.week_open for week in self.weeks]

    def offset_at(self, server_time: datetime) -> int | None:
        """Offset in hours for a server time, or None before the first measured week."""
        index = bisect_right(self._starts, server_time) - 1
        if index < 0:
            return None
        return self.weeks[index].offset_hours

    def to_utc(self, server_time: datetime) -> datetime | None:
        offset = self.offset_at(server_time)
        if offset is None:
            return None
        return (server_time - timedelta(hours=offset)).replace(tzinfo=UTC)

    def covers(self, start: date, end: date) -> bool:
        """True when every week from start up to (exclusive) end has a measurement or follows one.

        The current week counts as covered once its open has been measured, so a clock measured
        today covers any range ending today or earlier.
        """
        first = datetime(start.year, start.month, start.day)
        last_needed = datetime(end.year, end.month, end.day) - timedelta(days=1)
        return self.first_week <= first + timedelta(days=3) and (
            self.last_week + timedelta(days=7) > last_needed
        )

    def offsets_between(self, start: datetime, end: datetime) -> list[int]:
        found = {self.offset_at(start)}
        found.update(w.offset_hours for w in self.weeks if start <= w.week_open < end)
        return sorted(offset for offset in found if offset is not None)

    def to_json(self) -> str:
        return json.dumps(
            {
                "server": self.server,
                "reference_symbol": self.reference_symbol,
                "measured_at": self.measured_at.isoformat(),
                "weeks": [[w.week_open.isoformat(), w.offset_hours] for w in self.weeks],
            },
            indent=1,
        )

    @classmethod
    def from_json(cls, text: str) -> ServerClock:
        data = json.loads(text)
        return cls(
            server=data["server"],
            reference_symbol=data["reference_symbol"],
            weeks=tuple(
                WeekOffset(datetime.fromisoformat(start), int(offset))
                for start, offset in data["weeks"]
            ),
            measured_at=datetime.fromisoformat(data["measured_at"]),
        )


def cache_path(cache_dir: Path, server: str) -> Path:
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in server)
    return cache_dir / f"{safe}.json"


def load_cached(cache_dir: Path, server: str) -> ServerClock | None:
    try:
        return ServerClock.from_json(cache_path(cache_dir, server).read_text(encoding="utf-8"))
    except (OSError, ValueError, KeyError):
        return None


def save_cached(cache_dir: Path, clock: ServerClock) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path(cache_dir, clock.server).write_text(clock.to_json(), encoding="utf-8")


def describe(offsets: Iterable[int]) -> str:
    values = sorted(set(offsets))
    if not values:
        return "unknown"
    return " / ".join(f"UTC{offset:+d}" for offset in values)

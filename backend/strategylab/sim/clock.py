"""The simulated clock that strategies see instead of wall time.

Time only moves when the strategy sleeps. A sleep that would end before the next price arrives
ends at that price instead, because nothing observable can change in between: this lets a
script that polls every second get through a year of history in a minute or two. The clock never
moves past the end of the run; reaching it raises SimulationFinished inside the strategy.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

# A strategy that keeps calling the terminal without ever sleeping would spin forever at one
# moment of simulated time; after this many calls it is stopped with an explanation instead.
MAX_CALLS_WITHOUT_SLEEP = 200_000


class SimulationFinished(BaseException):
    """The history has run out. A BaseException, so a strategy's `except Exception` lets it pass."""


class SimulationStalledError(RuntimeError):
    pass


@dataclass
class SimClock:
    event_times_ms: np.ndarray
    now_ms: int
    end_ms: int
    advance: Callable[[int], None]
    """Called with the new time before the clock moves there, so orders and stops are handled."""
    finish: Callable[[], None]
    """Called once when the end is reached, before SimulationFinished is raised."""
    finished: bool = False
    calls_since_sleep: int = 0
    sleeps: int = field(default=0)
    next_index: int = field(default=-1)
    """The first price not yet reached; one stamped exactly at the start is still to come."""

    def __post_init__(self) -> None:
        if self.next_index < 0:
            self.next_index = int(np.searchsorted(self.event_times_ms, self.now_ms, side="left"))

    def touch(self) -> None:
        """Count a call made by the strategy; refuse to go on if it never sleeps."""
        if self.finished:
            raise SimulationFinished
        self.calls_since_sleep += 1
        if self.calls_since_sleep > MAX_CALLS_WITHOUT_SLEEP:
            raise SimulationStalledError(
                f"The strategy made {MAX_CALLS_WITHOUT_SLEEP} calls without sleeping. In the "
                "simulator time moves only in time.sleep(), so a loop that never sleeps never "
                "reaches the next price. Add a sleep to the polling loop."
            )

    def next_event_ms(self) -> int | None:
        if self.next_index >= len(self.event_times_ms):
            return None
        return int(self.event_times_ms[self.next_index])

    def sleep(self, seconds: float) -> None:
        if self.finished:
            raise SimulationFinished
        target = self.now_ms + max(0, round(float(seconds) * 1000))
        upcoming = self.next_event_ms()
        if upcoming is None:
            target = self.end_ms
        elif upcoming > target:
            # Nothing changes before the next price, so skipping to it is observably the same.
            target = upcoming
        target = min(target, self.end_ms)
        self.advance(target)
        self.now_ms = target
        self.next_index = int(np.searchsorted(self.event_times_ms, target, side="right"))
        self.calls_since_sleep = 0
        self.sleeps += 1
        if self.now_ms >= self.end_ms:
            self.finished = True
            self.finish()
            raise SimulationFinished

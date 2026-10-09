"""Price data the simulator moves through, and bars of any timeframe as of a simulated moment.

Times are server time in milliseconds since 1970, the convention MetaTrader uses for bar and
tick times. The price stream is either built from M1 bars, the way the Strategy Tester's
"1 minute OHLC" mode does it, or taken from recorded ticks.

Nothing here ever returns data stamped after the moment asked about: the last bar of any
timeframe is built only from the prices seen up to that moment, so its high, low and close
are what a live terminal would have shown then.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

import numpy as np

from strategylab.sim.constants import CONSTANTS

RATE_DTYPE = np.dtype(
    [
        ("time", "<i8"),
        ("open", "<f8"),
        ("high", "<f8"),
        ("low", "<f8"),
        ("close", "<f8"),
        ("tick_volume", "<u8"),
        ("spread", "<i4"),
        ("real_volume", "<u8"),
    ]
)
TICK_DTYPE = np.dtype(
    [
        ("time", "<i8"),
        ("bid", "<f8"),
        ("ask", "<f8"),
        ("last", "<f8"),
        ("volume", "<u8"),
        ("time_msc", "<i8"),
        ("flags", "<u4"),
        ("volume_real", "<f8"),
    ]
)
MINUTE_MS = 60_000
DAY_MS = 86_400_000
# 1970-01-01 was a Thursday; MetaTrader's weekly bars open on Sunday.
WEEK_OFFSET_MS = 3 * DAY_MS
TICK_FLAGS_BID_ASK = CONSTANTS["TICK_FLAG_BID"] | CONSTANTS["TICK_FLAG_ASK"]
# Where the synthetic prices of a minute sit inside it.
OFFSETS_MS = {1: (0,), 2: (0, 59_000), 3: (0, 30_000, 59_000), 4: (0, 20_000, 40_000, 59_000)}

TIMEFRAME_MS: dict[int, int] = {
    CONSTANTS[f"TIMEFRAME_M{m}"]: m * MINUTE_MS for m in (1, 2, 3, 4, 5, 6, 10, 12, 15, 20, 30)
}
TIMEFRAME_MS.update({CONSTANTS[f"TIMEFRAME_H{h}"]: h * 3_600_000 for h in (1, 2, 3, 4, 6, 8, 12)})
TIMEFRAME_MS[CONSTANTS["TIMEFRAME_D1"]] = DAY_MS
TIMEFRAME_MS[CONSTANTS["TIMEFRAME_W1"]] = 7 * DAY_MS
MONTHLY = CONSTANTS["TIMEFRAME_MN1"]
WEEKLY = CONSTANTS["TIMEFRAME_W1"]
TIMEFRAME_NAMES = {
    value: name.removeprefix("TIMEFRAME_")
    for name, value in CONSTANTS.items()
    if name.startswith("TIMEFRAME_")
}


def bucket_start(times_ms: np.ndarray, timeframe: int) -> np.ndarray:
    """Open time of the bar of `timeframe` that each time falls in."""
    if timeframe == MONTHLY:
        months = times_ms.astype("datetime64[ms]").astype("datetime64[M]")
        return months.astype("datetime64[ms]").astype(np.int64)
    period = TIMEFRAME_MS.get(timeframe)
    if period is None:
        raise ValueError(f"unknown timeframe {timeframe}")
    offset = WEEK_OFFSET_MS if timeframe == WEEKLY else 0
    return times_ms - (times_ms - offset) % period


@dataclass
class PriceStream:
    """Every price the simulation passes through, in time order."""

    time_ms: np.ndarray
    bid: np.ndarray
    ask: np.ndarray
    bar: np.ndarray
    """Index of the M1 bar each price belongs to."""
    first_of_bar: np.ndarray
    """True for the first price of its minute: reaching a level there means it was jumped over."""
    flags: np.ndarray
    last: np.ndarray
    volume: np.ndarray
    volume_real: np.ndarray
    synthetic: bool

    def __len__(self) -> int:
        return len(self.time_ms)


def _directions(m1: np.ndarray) -> np.ndarray:
    """+1 for rising bars, -1 for falling; a doji moves against the bar before it (help page)."""
    raw = np.sign(m1["close"] - m1["open"]).astype(np.int8)
    directions = raw.copy()
    previous = 1
    for index in np.flatnonzero(raw == 0):
        if index > 0:
            previous = directions[index - 1]
        directions[index] = -previous if previous else 1
    return directions


def synthetic_stream(m1: np.ndarray, point: float, digits: int) -> PriceStream:
    """Prices inside each minute as the Strategy Tester's "1 minute OHLC" mode generates them.

    Four prices per bar: open, then low and high (low first on a rising bar, high first on a
    falling one), then close. Bars with fewer ticks get fewer prices: three give open, the
    extreme that is neither open nor close, close; two give open and close; one gives the
    close only. Ask is bid plus the bar's recorded spread.
    """
    n = len(m1)
    o, h, l, c = (m1[k].astype(np.float64) for k in ("open", "high", "low", "close"))  # noqa: E741
    volume = np.clip(m1["tick_volume"].astype(np.int64), 1, 4)
    rising = _directions(m1) > 0

    prices = np.empty((n, 4))
    first_extreme = np.where(rising, l, h)
    second_extreme = np.where(rising, h, l)
    prices[:, 0], prices[:, 1], prices[:, 2], prices[:, 3] = o, first_extreme, second_extreme, c
    keep = np.zeros((n, 4), dtype=bool)
    keep[volume >= 4] = True
    three = volume == 3
    keep[three, 0] = keep[three, 3] = True
    above = h > np.maximum(o, c)
    below = l < np.minimum(o, c)
    # With three ticks only one extreme fits; take the one that lies outside open and close.
    use_high = three & (above | ~below) & ~(rising & below)
    prices[three, 1] = np.where(use_high[three], h[three], l[three])
    keep[three, 1] = True
    two = volume == 2
    keep[two, 0] = keep[two, 3] = True
    one = volume == 1
    prices[one, 0] = c[one]
    keep[one, 0] = True

    offsets = np.zeros((n, 4), dtype=np.int64)
    for count, positions in OFFSETS_MS.items():
        rows = volume == count
        columns = np.flatnonzero(keep[np.argmax(rows)]) if rows.any() else []
        for position, column in zip(positions, columns, strict=False):
            offsets[rows, column] = position

    bar_index = np.repeat(np.arange(n)[:, None], 4, axis=1)
    first = np.zeros((n, 4), dtype=bool)
    first[:, 0] = True
    time_ms = m1["time"].astype(np.int64)[:, None] * 1000 + offsets
    spread = (m1["spread"].astype(np.float64) * point)[:, None]
    bid = prices[keep]
    ask = np.round(prices + spread, digits)[keep]
    size = int(keep.sum())
    return PriceStream(
        time_ms=time_ms[keep],
        bid=bid,
        ask=ask,
        bar=bar_index[keep],
        first_of_bar=first[keep],
        flags=np.full(size, TICK_FLAGS_BID_ASK, dtype=np.uint32),
        last=np.zeros(size),
        volume=np.zeros(size, dtype=np.uint64),
        volume_real=np.zeros(size),
        synthetic=True,
    )


def tick_stream(ticks: np.ndarray, m1: np.ndarray) -> PriceStream:
    """Recorded ticks as the price stream; each tick is matched to the minute it falls in."""
    time_ms = ticks["time_msc"].astype(np.int64)
    bar = np.searchsorted(m1["time"].astype(np.int64) * 1000, time_ms, side="right") - 1
    valid = bar >= 0
    time_ms, bar = time_ms[valid], bar[valid]
    first = np.ones(len(bar), dtype=bool)
    first[1:] = bar[1:] != bar[:-1]
    return PriceStream(
        time_ms=time_ms,
        bid=ticks["bid"][valid].astype(np.float64),
        ask=ticks["ask"][valid].astype(np.float64),
        bar=bar,
        first_of_bar=first,
        flags=ticks["flags"][valid].astype(np.uint32),
        last=ticks["last"][valid].astype(np.float64),
        volume=ticks["volume"][valid].astype(np.uint64),
        volume_real=ticks["volume_real"][valid].astype(np.float64),
        synthetic=False,
    )


@dataclass
class _Aggregate:
    """Bars of one timeframe built from M1, plus the M1 range each one covers."""

    bars: np.ndarray
    first_m1: np.ndarray
    m1_bucket: np.ndarray
    """For every M1 bar, the index of the bar it belongs to."""
    history: np.ndarray
    """Bars from before the M1 data, if this timeframe was exported natively."""


@dataclass
class Market:
    symbol: str
    m1: np.ndarray
    stream: PriceStream
    native: dict[int, np.ndarray] = field(default_factory=dict)
    _aggregates: dict[int, _Aggregate] = field(default_factory=dict)
    _first_event_of_bar: np.ndarray = field(default_factory=lambda: np.zeros(0, np.int64))
    _events_in_bar: np.ndarray = field(default_factory=lambda: np.zeros(0, np.int64))

    def __post_init__(self) -> None:
        starts = np.flatnonzero(self.stream.first_of_bar)
        lookup = np.full(len(self.m1), -1, dtype=np.int64)
        lookup[self.stream.bar[starts]] = starts
        self._first_event_of_bar = lookup
        self._events_in_bar = np.bincount(self.stream.bar, minlength=len(self.m1))

    # --- the price at an index of the stream ---------------------------------------------------

    def index_at(self, time_ms: int) -> int:
        """Index of the last price at or before `time_ms`; -1 if there is none yet."""
        return int(np.searchsorted(self.stream.time_ms, time_ms, side="right")) - 1

    # --- bars ----------------------------------------------------------------------------------

    def _aggregate(self, timeframe: int) -> _Aggregate:
        cached = self._aggregates.get(timeframe)
        if cached is not None:
            return cached
        m1_ms = self.m1["time"].astype(np.int64) * 1000
        buckets = bucket_start(m1_ms, timeframe)
        starts = np.flatnonzero(np.r_[True, buckets[1:] != buckets[:-1]]) if len(m1_ms) else []
        starts = np.asarray(starts, dtype=np.int64)
        ends = np.r_[starts[1:], len(m1_ms)].astype(np.int64)
        bars = np.zeros(len(starts), dtype=RATE_DTYPE)
        if len(starts):
            bars["time"] = buckets[starts] // 1000
            bars["open"] = self.m1["open"][starts]
            bars["close"] = self.m1["close"][ends - 1]
            bars["high"] = np.maximum.reduceat(self.m1["high"], starts)
            bars["low"] = np.minimum.reduceat(self.m1["low"], starts)
            bars["tick_volume"] = np.add.reduceat(self.m1["tick_volume"].astype(np.uint64), starts)
            bars["real_volume"] = np.add.reduceat(self.m1["real_volume"].astype(np.uint64), starts)
            bars["spread"] = self.m1["spread"][starts]
        m1_bucket = np.repeat(np.arange(len(starts)), ends - starts)
        history = self.native.get(timeframe, np.zeros(0, dtype=RATE_DTYPE))
        if len(history) and len(bars):
            history = history[history["time"] < bars["time"][0]]
        aggregate = _Aggregate(bars, starts, m1_bucket, history)
        self._aggregates[timeframe] = aggregate
        return aggregate

    def _forming_bar(self, aggregate: _Aggregate, bucket: int, event: int) -> np.ndarray:
        """The bar `bucket` as seen at stream index `event`: built from prices seen so far."""
        stream = self.stream
        current_m1 = int(stream.bar[event])
        first_m1 = int(aggregate.first_m1[bucket])
        bar = np.zeros(1, dtype=RATE_DTYPE)[0]
        bar["time"] = aggregate.bars["time"][bucket]
        bar["spread"] = self.m1["spread"][first_m1]
        first_event = int(self._first_event_of_bar[current_m1])
        seen = stream.bid[first_event : event + 1]
        partial_volume = int(self.m1["tick_volume"][current_m1])
        if stream.synthetic:
            in_minute = int(self._events_in_bar[current_m1]) or 1
            partial_volume = max(1, round(partial_volume * len(seen) / in_minute))
        else:
            partial_volume = len(seen)
        highs, lows = [seen.max()], [seen.min()]
        volume = partial_volume
        if current_m1 > first_m1:
            complete = self.m1[first_m1:current_m1]
            bar["open"] = complete["open"][0]
            highs.append(complete["high"].max())
            lows.append(complete["low"].min())
            volume += int(complete["tick_volume"].sum())
        else:
            bar["open"] = self.m1["open"][current_m1] if stream.synthetic else seen[0]
        bar["high"] = max(highs)
        bar["low"] = min(lows)
        bar["close"] = seen[-1]
        bar["tick_volume"] = volume
        return bar

    def bars_at(self, timeframe: int, event: int) -> _BarView:
        """Every bar of `timeframe` visible at stream index `event`, oldest first."""
        aggregate = self._aggregate(timeframe)
        if event < 0:
            return _BarView(aggregate.history, aggregate.bars[:0], None)
        bucket = int(aggregate.m1_bucket[int(self.stream.bar[event])])
        forming = self._forming_bar(aggregate, bucket, event)
        return _BarView(aggregate.history, aggregate.bars[:bucket], forming)

    # --- ticks ---------------------------------------------------------------------------------

    def ticks(self, start: int, stop: int) -> np.ndarray:
        stream = self.stream
        out = np.zeros(max(0, stop - start), dtype=TICK_DTYPE)
        if len(out):
            out["time_msc"] = stream.time_ms[start:stop]
            out["time"] = out["time_msc"] // 1000
            out["bid"] = stream.bid[start:stop]
            out["ask"] = stream.ask[start:stop]
            out["last"] = stream.last[start:stop]
            out["volume"] = stream.volume[start:stop]
            out["flags"] = stream.flags[start:stop]
            out["volume_real"] = stream.volume_real[start:stop]
        return out


@dataclass
class _BarView:
    """History bars, complete bars and the forming bar, addressable as one series."""

    history: np.ndarray
    complete: np.ndarray
    forming: np.void | None

    def __len__(self) -> int:
        return len(self.history) + len(self.complete) + (self.forming is not None)

    def count_until(self, seconds: int) -> int:
        """How many bars open at or before `seconds`."""
        count = int(np.searchsorted(self.history["time"], seconds, side="right"))
        if count < len(self.history):
            return count
        count += int(np.searchsorted(self.complete["time"], seconds, side="right"))
        visible_complete = len(self.history) + len(self.complete)
        if count == visible_complete and self.forming is not None:
            count += int(self.forming["time"] <= seconds)
        return count

    def slice(self, start: int, stop: int) -> np.ndarray:
        start, stop = max(0, start), min(len(self), stop)
        if stop <= start:
            return np.zeros(0, dtype=RATE_DTYPE)
        h, c = len(self.history), len(self.complete)
        parts = []
        if start < h:
            parts.append(self.history[start : min(stop, h)])
        if stop > h and start < h + c:
            parts.append(self.complete[max(0, start - h) : min(stop - h, c)])
        if self.forming is not None and stop == h + c + 1:
            parts.append(np.array([self.forming], dtype=RATE_DTYPE))
        return np.concatenate(parts) if parts else np.zeros(0, dtype=RATE_DTYPE)


def server_datetime(time_ms: int) -> datetime:
    return datetime.fromtimestamp(time_ms / 1000, UTC).replace(tzinfo=None)

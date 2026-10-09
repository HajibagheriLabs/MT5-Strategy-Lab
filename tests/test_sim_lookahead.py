"""No function a strategy can call may reveal anything stamped after the simulated clock."""

from datetime import datetime

import numpy as np
import pytest
from simdata import bars, ms, simulation

from strategylab.sim.constants import CONSTANTS
from strategylab.sim.market import bucket_start

SYMBOL = "EURUSD@"
TIMEFRAMES = [
    CONSTANTS[f"TIMEFRAME_{name}"] for name in ("M1", "M5", "M15", "H1", "H4", "D1", "W1")
]
FAR_FUTURE = datetime(2030, 1, 1)
ALL = CONSTANTS["COPY_TICKS_ALL"]


def random_walk(days=3, seed=7):
    rng = np.random.default_rng(seed)
    count = days * 1440
    closes = 1.1 + np.cumsum(rng.normal(0, 2e-4, count))
    opens = np.r_[1.1, closes[:-1]]
    highs = np.maximum(opens, closes) + rng.uniform(0, 3e-4, count)
    lows = np.minimum(opens, closes) - rng.uniform(0, 3e-4, count)
    rows = [tuple(round(x, 5) for x in row) for row in zip(opens, highs, lows, closes, strict=True)]
    return bars(datetime(2025, 1, 6), rows, volume=6)


@pytest.fixture(scope="module")
def walk():
    return random_walk()


def expected_forming(market, timeframe, index):
    """The last bar recomputed from scratch from the prices at or before `index`."""
    stream = market.stream
    times = stream.time_ms[: index + 1]
    bucket = bucket_start(times[-1:], timeframe)[0]
    in_bucket = bucket_start(times, timeframe) == bucket
    seen = stream.bid[: index + 1][in_bucket]
    first_bar = int(stream.bar[: index + 1][in_bucket][0])
    return bucket // 1000, market.m1["open"][first_bar], seen.max(), seen.min(), seen[-1]


def test_nothing_from_the_future_at_any_moment(walk):
    market, broker, clock, terminal = simulation(walk, ms(2025, 1, 6, 6), ms(2025, 1, 9))
    rng = np.random.default_rng(1)
    targets = np.sort(rng.integers(ms(2025, 1, 6, 6), ms(2025, 1, 8, 23), 150))
    for target in targets:
        if target <= clock.now_ms:
            continue
        clock.sleep((target - clock.now_ms) / 1000)
        now = clock.now_ms
        index = broker.index
        assert int(market.stream.time_ms[index]) <= now

        for timeframe in TIMEFRAMES:
            for rates in (
                terminal.rates_from_pos(timeframe, 0, 5000),
                terminal.rates_range(timeframe, datetime(2024, 1, 1), FAR_FUTURE),
                terminal.rates_from(timeframe, FAR_FUTURE, 5000),
            ):
                assert rates is not None
                assert rates["time"].max() * 1000 <= now
            last = terminal.rates_from_pos(timeframe, 0, 1)[0]
            time, open_, high, low, close = expected_forming(market, timeframe, index)
            # The bar that is still forming shows only what has happened so far.
            assert last["time"] == time
            assert (last["high"], last["low"], last["close"]) == (high, low, close)
            if timeframe != CONSTANTS["TIMEFRAME_M1"] or market.stream.synthetic:
                assert last["open"] == open_

        for ticks in (
            terminal.ticks_range(datetime(2024, 1, 1), FAR_FUTURE, ALL),
            terminal.ticks_from(datetime(2024, 1, 1), 10**9, ALL),
        ):
            assert ticks["time_msc"].max() <= now
        tick = terminal.tick()
        assert tick.time_msc <= now
        info = terminal.symbol_info()
        assert info.time * 1000 <= now
        seen_today = market.stream.bid[: index + 1][
            market.stream.time_ms[: index + 1] // 86_400_000 == now // 86_400_000
        ]
        if len(seen_today):
            assert info.bidhigh == seen_today.max()


def test_a_spike_later_in_the_hour_is_invisible():
    rows = [(1.1000, 1.1002, 1.0998, 1.1000)] * 60
    rows[45] = (1.1000, 1.1500, 1.0998, 1.1000)  # a spike at 10:45
    m1 = bars(datetime(2025, 1, 6, 10, 0), rows)
    _, _, clock, terminal = simulation(m1, ms(2025, 1, 6, 10, 0), ms(2025, 1, 6, 11, 0))
    clock.sleep(30 * 60)  # 10:30
    h1 = terminal.rates_from_pos(CONSTANTS["TIMEFRAME_H1"], 0, 1)[0]
    d1 = terminal.rates_from_pos(CONSTANTS["TIMEFRAME_D1"], 0, 1)[0]
    assert h1["high"] < 1.11
    assert d1["high"] < 1.11
    assert terminal.symbol_info().bidhigh < 1.11
    clock.sleep(16 * 60)  # 10:46
    assert terminal.rates_from_pos(CONSTANTS["TIMEFRAME_H1"], 0, 1)[0]["high"] == 1.15


def test_symbol_info_does_not_leak_export_time_values():
    m1 = bars(datetime(2025, 1, 6, 10, 0), [(1.1, 1.1, 1.1, 1.1)] * 5)
    _, _, clock, terminal = simulation(m1, ms(2025, 1, 6, 10, 0), ms(2025, 1, 6, 11, 0))
    clock.sleep(60)
    info = terminal.symbol_info()
    # The spec as exported says time 2 000 000 000 (2033), bid 9.99 and price_change 99.
    assert info.time == ms(2025, 1, 6, 10, 1) // 1000
    assert info.bid == 1.1
    assert info.price_change == 0


def test_before_the_first_price_nothing_is_returned():
    m1 = bars(datetime(2025, 1, 6, 10, 0), [(1.1, 1.1, 1.1, 1.1)] * 5)
    _, _, _, terminal = simulation(m1, ms(2025, 1, 6, 9, 0), ms(2025, 1, 6, 11, 0))
    assert terminal.rates_from_pos(CONSTANTS["TIMEFRAME_M1"], 0, 10) is None
    assert terminal.tick() is None
    assert terminal.ticks_range(datetime(2024, 1, 1), FAR_FUTURE, ALL) is None

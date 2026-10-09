from datetime import datetime

import numpy as np
import pytest
from simdata import DIGITS, POINT, bars, flat_bars, ms, seconds

from strategylab.sim.constants import CONSTANTS
from strategylab.sim.market import (
    TICK_DTYPE,
    Market,
    bucket_start,
    synthetic_stream,
    tick_stream,
)

H1 = CONSTANTS["TIMEFRAME_H1"]
D1 = CONSTANTS["TIMEFRAME_D1"]
W1 = CONSTANTS["TIMEFRAME_W1"]
MN1 = CONSTANTS["TIMEFRAME_MN1"]
M5 = CONSTANTS["TIMEFRAME_M5"]
START = datetime(2025, 1, 6, 10, 0)


def prices(m1):
    return list(synthetic_stream(m1, POINT, DIGITS).bid)


class TestMinutePath:
    def test_rising_bar_visits_low_then_high(self):
        m1 = bars(START, [(1.1000, 1.1010, 1.0990, 1.1005)])
        assert prices(m1) == [1.1000, 1.0990, 1.1010, 1.1005]

    def test_falling_bar_visits_high_then_low(self):
        m1 = bars(START, [(1.1000, 1.1010, 1.0990, 1.0995)])
        assert prices(m1) == [1.1000, 1.1010, 1.0990, 1.0995]

    def test_doji_moves_against_the_bar_before_it(self):
        m1 = bars(
            START,
            [
                (1.1000, 1.1010, 1.0990, 1.1005),  # rising
                (1.1000, 1.1010, 1.0990, 1.1000),  # doji, taken as falling
                (1.1000, 1.1010, 1.0990, 1.1000),  # doji after a "falling" doji, rising
            ],
        )
        assert prices(m1)[4:] == [1.1000, 1.1010, 1.0990, 1.1000, 1.1000, 1.0990, 1.1010, 1.1000]

    def test_bars_with_few_ticks(self):
        one = bars(START, [(1.1000, 1.1010, 1.0990, 1.1005)], volume=1)
        two = bars(START, [(1.1000, 1.1010, 1.0990, 1.1005)], volume=2)
        three_high = bars(START, [(1.1000, 1.1010, 1.1000, 1.1005)], volume=3)
        three_low = bars(START, [(1.1000, 1.1005, 1.0990, 1.1005)], volume=3)
        three_inside = bars(START, [(1.1000, 1.1005, 1.1000, 1.1005)], volume=3)
        assert prices(one) == [1.1005]
        assert prices(two) == [1.1000, 1.1005]
        assert prices(three_high) == [1.1000, 1.1010, 1.1005]
        assert prices(three_low) == [1.1000, 1.0990, 1.1005]
        assert prices(three_inside) == [1.1000, 1.1005]

    @pytest.mark.parametrize(
        ("volume", "row", "seconds"),
        [
            (1, (1.1, 1.2, 1.0, 1.15), [30]),
            (2, (1.1, 1.2, 1.0, 1.15), [0, 30]),
            (3, (1.1, 1.2, 1.1, 1.15), [0, 30, 59]),
            (3, (1.1, 1.15, 1.1, 1.15), [0, 59]),
            (4, (1.1, 1.2, 1.0, 1.15), [0, 20, 40, 59]),
            (90, (1.1, 1.2, 1.0, 1.15), [0, 20, 40, 59]),
        ],
    )
    def test_times_inside_the_minute_match_the_tester(self, volume, row, seconds):
        stream = synthetic_stream(bars(START, [row], volume=volume), POINT, DIGITS)
        base = ms(2025, 1, 6, 10, 0)
        assert list((stream.time_ms - base) // 1000) == seconds
        assert list(stream.first_of_bar) == [True] + [False] * (len(seconds) - 1)

    def test_ask_is_bid_plus_the_minute_spread(self):
        stream = synthetic_stream(
            bars(START, [(1.10001, 1.10011, 1.09991, 1.10006)], spread=12), POINT, DIGITS
        )
        assert list(stream.ask) == [1.10013, 1.10003, 1.10023, 1.10018]


class TestBuckets:
    def test_hours_days_weeks_and_months(self):
        t = np.array([ms(2025, 1, 8, 13, 47)], dtype=np.int64)  # a Wednesday
        assert bucket_start(t, H1)[0] == ms(2025, 1, 8, 13, 0)
        assert bucket_start(t, D1)[0] == ms(2025, 1, 8)
        assert bucket_start(t, W1)[0] == ms(2025, 1, 5)  # the Sunday before
        assert bucket_start(t, MN1)[0] == ms(2025, 1, 1)
        assert bucket_start(t, M5)[0] == ms(2025, 1, 8, 13, 45)


class TestBars:
    def test_complete_hours_are_built_from_minutes(self):
        rows = [
            (1.1 + i * 1e-4, 1.1 + i * 1e-4 + 5e-5, 1.1 + i * 1e-4 - 5e-5, 1.1 + i * 1e-4)
            for i in range(130)
        ]
        m1 = bars(START, rows)
        market = Market("EURUSD@", m1, synthetic_stream(m1, POINT, DIGITS))
        view = market.bars_at(H1, len(market.stream) - 1)
        assert len(view) == 3
        first = view.slice(0, 1)[0]
        assert first["time"] == seconds(2025, 1, 6, 10)
        assert first["open"] == m1["open"][0]
        assert first["close"] == m1["close"][59]
        assert first["high"] == m1["high"][:60].max()
        assert first["low"] == m1["low"][:60].min()
        assert first["tick_volume"] == 600

    def test_history_bars_come_before_the_minutes(self):
        m1 = flat_bars(START, 60)
        history = flat_bars(datetime(2025, 1, 6, 5, 0), 6, step_minutes=60)
        market = Market("EURUSD@", m1, synthetic_stream(m1, POINT, DIGITS), {H1: history})
        view = market.bars_at(H1, len(market.stream) - 1)
        times = view.slice(0, len(view))["time"]
        assert list(times) == [seconds(2025, 1, 6, h) for h in range(5, 11)]
        assert view.count_until(seconds(2025, 1, 6, 7, 30)) == 3

    def test_nothing_before_the_first_price(self):
        m1 = flat_bars(START, 5)
        market = Market("EURUSD@", m1, synthetic_stream(m1, POINT, DIGITS))
        assert len(market.bars_at(H1, -1)) == 0


class TestRecordedTicks:
    def test_ticks_are_matched_to_their_minute(self):
        m1 = flat_bars(START, 3)
        ticks = np.zeros(4, dtype=TICK_DTYPE)
        times = [
            ms(2025, 1, 6, 10, 0, 5),
            ms(2025, 1, 6, 10, 0, 50),
            ms(2025, 1, 6, 10, 2, 1),
            ms(2025, 1, 6, 10, 2, 30),
        ]
        ticks["time_msc"] = times
        ticks["time"] = np.array(times) // 1000
        ticks["bid"] = [1.1, 1.2, 1.3, 1.4]
        ticks["ask"] = [1.1001, 1.2001, 1.3001, 1.4001]
        stream = tick_stream(ticks, m1)
        assert list(stream.bar) == [0, 0, 2, 2]
        assert list(stream.first_of_bar) == [True, False, True, False]
        assert not stream.synthetic


@pytest.mark.parametrize("volume", [1, 2, 3, 4, 50])
def test_every_minute_produces_prices_in_time_order(volume):
    m1 = bars(START, [(1.1, 1.2, 1.0, 1.15), (1.15, 1.16, 1.14, 1.14)], volume=volume)
    stream = synthetic_stream(m1, POINT, DIGITS)
    assert np.all(np.diff(stream.time_ms) > 0)
    assert set(stream.bar) == {0, 1}

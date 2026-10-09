from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from strategylab.server_clock import (
    ClockError,
    ServerClock,
    WeekOffset,
    detect_week_offsets,
    fx_week_open_utc,
    load_cached,
    save_cached,
)

NY = ZoneInfo("America/New_York")
EU = ZoneInfo("Europe/Athens")


def server_bars(start: date, end: date, offset_for) -> list[datetime]:
    """H1 bar times of an FX symbol on a server whose UTC offset is offset_for(utc_time).

    The market trades from Sunday 17:00 New York to Friday 17:00 New York and closes from noon on
    24 and 31 December until the day after the holiday, like real FX data.
    """
    times = []
    t = datetime(start.year, start.month, start.day, tzinfo=UTC)
    stop = datetime(end.year, end.month, end.day, tzinfo=UTC)
    while t < stop:
        ny = t.astimezone(NY)
        weekday, hour = ny.weekday(), ny.hour
        open_ = not (
            weekday == 5
            or (weekday == 4 and hour >= 17)
            or (weekday == 6 and hour < 17)
            or (ny.month, ny.day) in ((12, 25), (1, 1))
            or ((ny.month, ny.day) in ((12, 24), (12, 31)) and hour >= 12)
        )
        if open_:
            server = t + timedelta(hours=offset_for(t))
            times.append(server.replace(tzinfo=None))
        t += timedelta(hours=1)
    return times


def us_rule(t):  # UTC+2, UTC+3 while New York is on daylight saving time
    return 7 + int(t.astimezone(NY).utcoffset().total_seconds() // 3600)


def eu_rule(t):  # UTC+2, UTC+3 while Europe is on summer time
    return int(t.astimezone(EU).utcoffset().total_seconds() // 3600)


class TestDetection:
    def test_week_open_reference(self):
        # Sunday 2025-01-05 17:00 New York is 22:00 UTC (winter), 2025-06-08 is 21:00 UTC.
        assert fx_week_open_utc(datetime(2025, 1, 6, 0)) == datetime(2025, 1, 5, 22)
        assert fx_week_open_utc(datetime(2025, 6, 8, 23)) == datetime(2025, 6, 8, 21)

    def test_us_rule_broker(self):
        weeks = detect_week_offsets(server_bars(date(2025, 2, 1), date(2025, 4, 1), us_rule))
        by_week = {w.week_open.date(): w.offset_hours for w in weeks}
        # US clocks changed on 9 March 2025; this broker's week always opens at 00:00 Monday.
        assert by_week[date(2025, 3, 3)] == 2
        assert by_week[date(2025, 3, 10)] == 3
        assert all(w.week_open.hour == 0 for w in weeks)

    def test_eu_rule_broker(self):
        weeks = detect_week_offsets(server_bars(date(2025, 2, 1), date(2025, 4, 15), eu_rule))
        offsets = {w.week_open: w.offset_hours for w in weeks}
        # Between the US change (9 March) and the EU change (30 March) the week opens at 23:00
        # Sunday server time, and the offset is still +2.
        assert offsets[datetime(2025, 3, 16, 23)] == 2
        assert offsets[datetime(2025, 3, 31, 0)] == 3

    def test_holiday_gaps_are_not_weekly_opens(self):
        bars = server_bars(date(2025, 12, 15), date(2026, 1, 15), us_rule)
        weeks = detect_week_offsets(bars)
        assert all(w.week_open.weekday() in (0, 6) for w in weeks)
        assert {w.offset_hours for w in weeks} == {2}

    def test_late_open_after_a_holiday_is_discarded(self):
        bars = server_bars(date(2025, 1, 6), date(2025, 2, 10), us_rule)
        monday = datetime(2025, 1, 20)  # pretend the market opened six hours late that week
        bars = [b for b in bars if not (monday <= b < monday + timedelta(hours=6))]
        weeks = detect_week_offsets(bars)
        assert monday + timedelta(hours=6) not in [w.week_open for w in weeks]
        assert {w.offset_hours for w in weeks} == {2}

    def test_no_weekends_no_weeks(self):
        continuous = [datetime(2025, 1, 1) + timedelta(hours=h) for h in range(24 * 30)]
        assert detect_week_offsets(continuous) == []
        with pytest.raises(ClockError, match="cannot be measured"):
            ServerClock.from_bars("S", "BTCUSD", continuous)


class TestConversion:
    @pytest.fixture
    def clock(self):
        bars = server_bars(date(2024, 12, 1), date(2025, 12, 31), us_rule)
        return ServerClock.from_bars("Broker-Demo", "EURUSD", bars)

    def test_round_trip_against_the_rule(self, clock):
        for server_time in (
            datetime(2025, 1, 2, 12, 0),
            datetime(2025, 3, 7, 23, 59, 59),  # last minutes before the US change
            datetime(2025, 3, 10, 0, 0),
            datetime(2025, 7, 1, 9, 30),
            datetime(2025, 11, 3, 0, 0),
        ):
            utc = clock.to_utc(server_time)
            assert utc.tzinfo is UTC
            assert utc + timedelta(hours=us_rule(utc)) == server_time.replace(tzinfo=UTC)

    def test_before_the_first_week_is_unknown(self, clock):
        assert clock.to_utc(datetime(2024, 11, 1)) is None

    def test_offsets_between(self, clock):
        assert clock.offsets_between(datetime(2025, 1, 6), datetime(2025, 2, 1)) == [2]
        assert clock.offsets_between(datetime(2025, 1, 6), datetime(2025, 6, 1)) == [2, 3]

    def test_covers(self, clock):
        assert clock.covers(date(2025, 2, 1), date(2025, 12, 1))
        assert not clock.covers(date(2024, 6, 1), date(2025, 2, 1))
        assert clock.covers(date(2025, 1, 1), date(2025, 1, 10))
        assert not clock.covers(date(2025, 2, 1), date(2026, 6, 1))

    def test_cache_round_trip(self, clock, tmp_path):
        save_cached(tmp_path, clock)
        loaded = load_cached(tmp_path, "Broker-Demo")
        assert loaded.weeks == clock.weeks
        assert loaded.reference_symbol == "EURUSD"
        assert load_cached(tmp_path, "Other-Server") is None

    def test_cache_file_name_is_safe(self, tmp_path):
        clock = ServerClock("Broker:Live/1", "EURUSD", (WeekOffset(datetime(2025, 1, 6), 2),),
                            datetime.now(UTC))  # fmt: skip
        save_cached(tmp_path, clock)
        assert [p.name for p in tmp_path.iterdir()] == ["Broker_Live_1.json"]

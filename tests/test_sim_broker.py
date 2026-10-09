from datetime import datetime

import pytest
from simdata import bars, flat_bars, ms, simulation, spec

from strategylab.sim.broker import SimulatorUnsupportedError
from strategylab.sim.constants import CONSTANTS as C

BUY, SELL = C["ORDER_TYPE_BUY"], C["ORDER_TYPE_SELL"]
FOK = C["ORDER_FILLING_FOK"]
START = datetime(2025, 1, 6, 10, 0)  # a Monday
FLAT = (1.1000, 1.1000, 1.1000, 1.1000)


def market_order(order_type, volume=1.0, **extra):
    request = {
        "action": C["TRADE_ACTION_DEAL"],
        "symbol": "EURUSD@",
        "type": order_type,
        "volume": volume,
        "type_filling": FOK,
    }
    request.update(extra)
    return request


def pending(order_type, price, volume=1.0, **extra):
    request = {
        "action": C["TRADE_ACTION_PENDING"],
        "symbol": "EURUSD@",
        "type": order_type,
        "volume": volume,
        "price": price,
    }
    request.update(extra)
    return request


def run(rows, start=START, minutes_after=10, **kwargs):
    m1 = bars(start, rows)
    start_ms = int(start.timestamp()) * 1000 if start.tzinfo else ms(*start.timetuple()[:5])
    end_ms = start_ms + (len(rows) + minutes_after) * 60_000
    return simulation(m1, start_ms, end_ms, **kwargs)


def first_minute(clock):
    clock.sleep(0)


class TestMarketOrders:
    def test_buy_at_ask_sell_at_bid(self):
        _, broker, clock, _ = run([FLAT] * 3)
        first_minute(clock)
        bought = broker.send(market_order(BUY))
        sold = broker.send(market_order(SELL))
        assert (bought.retcode, bought.price) == (C["TRADE_RETCODE_DONE"], 1.1001)
        assert (sold.retcode, sold.price) == (C["TRADE_RETCODE_DONE"], 1.1000)
        assert [d.entry for d in broker.deals[1:]] == [C["DEAL_ENTRY_IN"]] * 2
        assert len(broker.positions) == 2

    def test_closing_a_position_books_its_profit(self):
        rows = [FLAT, FLAT, (1.1050, 1.1050, 1.1050, 1.1050)]
        _, broker, clock, _ = run(rows)
        first_minute(clock)
        opened = broker.send(market_order(BUY, 0.5))
        clock.sleep(120)
        closed = broker.send(market_order(SELL, 0.5, position=opened.order))
        deal = broker.deals[-1]
        assert closed.price == 1.1050
        assert deal.entry == C["DEAL_ENTRY_OUT"]
        assert deal.profit == pytest.approx((1.1050 - 1.1001) * 0.5 * 100_000)
        assert broker.balance == pytest.approx(10_000 + deal.profit)
        assert broker.positions == []

    @pytest.mark.parametrize(
        ("request_changes", "retcode"),
        [
            ({"type_filling": C["ORDER_FILLING_IOC"]}, "TRADE_RETCODE_INVALID_FILL"),
            ({"type_filling": C["ORDER_FILLING_RETURN"]}, "TRADE_RETCODE_INVALID_FILL"),
            ({"volume": 0.015}, "TRADE_RETCODE_INVALID_VOLUME"),
            ({"volume": 0.001}, "TRADE_RETCODE_INVALID_VOLUME"),
            ({"volume": 60.0}, "TRADE_RETCODE_INVALID_VOLUME"),
            ({"sl": 1.09995}, "TRADE_RETCODE_INVALID_STOPS"),
            ({"tp": 1.10005}, "TRADE_RETCODE_INVALID_STOPS"),
            ({"volume": 20.0}, "TRADE_RETCODE_NO_MONEY"),
            ({"position": 999}, "TRADE_RETCODE_POSITION_CLOSED"),
        ],
    )
    def test_rejections_match_the_tester(self, request_changes, retcode):
        _, broker, clock, _ = run([FLAT] * 3)
        first_minute(clock)
        outcome = broker.send(market_order(BUY, **request_changes))
        assert outcome.retcode == C[retcode]
        assert broker.deals[1:] == []

    def test_no_price_before_the_first_minute(self):
        _, broker, _, _ = run([FLAT] * 3, minutes_after=0)
        broker.index = -1
        assert broker.send(market_order(BUY)).retcode == C["TRADE_RETCODE_PRICE_OFF"]

    def test_order_check_changes_nothing(self):
        _, broker, clock, _ = run([FLAT] * 3)
        first_minute(clock)
        retcode, margin = broker.check(market_order(BUY))
        assert retcode == 0
        assert margin == pytest.approx(1.1001 * 100_000 / 100)
        assert broker.positions == [] and len(broker.deals) == 1
        assert broker.check(market_order(BUY, volume=0.015))[0] == C["TRADE_RETCODE_INVALID_VOLUME"]

    def test_unsupported_requests_raise(self):
        _, broker, clock, _ = run([FLAT] * 3)
        first_minute(clock)
        with pytest.raises(SimulatorUnsupportedError, match="stop-limit"):
            broker.send(pending(C["ORDER_TYPE_BUY_STOP_LIMIT"], 1.1020))
        with pytest.raises(SimulatorUnsupportedError, match="CLOSE_BY"):
            broker.send({"action": C["TRADE_ACTION_CLOSE_BY"]})


class TestStopsInsideAMinute:
    def open_long(self, rows):
        _, broker, clock, _ = run([FLAT, *rows])
        first_minute(clock)
        broker.send(market_order(BUY, sl=1.0990, tp=1.1010))
        clock.sleep(60 * (len(rows) + 1))
        return broker

    def test_rising_bar_reaches_the_low_first(self):
        broker = self.open_long([(1.1000, 1.1020, 1.0980, 1.1015)])
        deal = broker.deals[-1]
        assert (deal.price, deal.comment, deal.reason) == (
            1.0990,
            "sl 1.09900",
            C["DEAL_REASON_SL"],
        )

    def test_falling_bar_reaches_the_high_first(self):
        broker = self.open_long([(1.1000, 1.1020, 1.0980, 1.0985)])
        deal = broker.deals[-1]
        assert (deal.price, deal.comment, deal.reason) == (
            1.1010,
            "tp 1.10100",
            C["DEAL_REASON_TP"],
        )

    def test_a_gap_still_fills_at_the_level_on_minute_bars(self):
        # The tester's "1 minute OHLC" mode fills at the level even when the minute opens
        # beyond it; the parity study found every one of its stop and target exits there.
        broker = self.open_long([(1.0950, 1.0960, 1.0940, 1.0955)])
        deal = broker.deals[-1]
        assert (deal.price, deal.comment) == (1.0990, "sl 1.09900")

    def test_short_levels_are_watched_on_the_ask(self):
        rows = [FLAT, (1.1000, 1.1010, 1.1000, 1.1005), (1.1005, 1.1012, 1.1004, 1.1006)]
        _, broker, clock, _ = run(rows)
        first_minute(clock)
        broker.send(market_order(SELL, sl=1.1012))
        clock.sleep(120)
        # The bid only reached 1.1010, but the ask (bid + 1 pip) touched 1.1011: still open.
        assert len(broker.positions) == 1
        clock.sleep(60)
        assert broker.positions == []
        assert broker.deals[-1].price == 1.1012


def test_on_real_ticks_a_stop_fills_at_the_crossing_tick():
    import numpy as np
    from simdata import POINT, spec

    from strategylab.sim.broker import AccountSettings, Broker
    from strategylab.sim.clock import SimClock
    from strategylab.sim.market import TICK_DTYPE, Market, tick_stream

    m1 = bars(START, [FLAT, FLAT])
    ticks = np.zeros(3, dtype=TICK_DTYPE)
    times = [ms(2025, 1, 6, 10, 0, 1), ms(2025, 1, 6, 10, 0, 30), ms(2025, 1, 6, 10, 1, 5)]
    ticks["time_msc"] = times
    ticks["time"] = np.array(times) // 1000
    ticks["bid"] = [1.1000, 1.1000, 1.0985]  # the second tick jumps 5 points past the stop
    ticks["ask"] = ticks["bid"] + 10 * POINT
    market = Market("EURUSD@", m1, tick_stream(ticks, m1))
    broker = Broker(market, spec(), AccountSettings(10_000, "USD", 100), ms(2025, 1, 6, 10))
    clock = SimClock(market.stream.time_ms, ms(2025, 1, 6, 10), ms(2025, 1, 6, 10, 5),
                     lambda t: broker.advance(t, ms(2025, 1, 6, 10, 5)), broker.finish)  # fmt: skip
    clock.sleep(0)
    broker.send(market_order(BUY, sl=1.0990))
    clock.sleep(120)
    assert (broker.deals[-1].price, broker.deals[-1].comment) == (1.0985, "sl 1.09900")


class TestPendingOrders:
    def test_buy_limit_fills_at_its_price(self):
        rows = [FLAT, (1.1000, 1.1000, 1.0985, 1.0995)]
        _, broker, clock, _ = run(rows)
        first_minute(clock)
        placed = broker.send(pending(C["ORDER_TYPE_BUY_LIMIT"], 1.0990))
        assert placed.retcode == C["TRADE_RETCODE_PLACED"]
        clock.sleep(120)
        assert broker.orders == []
        assert broker.positions[0].price_open == 1.0990
        assert broker.history_orders[-1].state == C["ORDER_STATE_FILLED"]

    def test_buy_stop_after_a_gap_fills_at_its_price_on_minute_bars(self):
        rows = [FLAT, (1.1030, 1.1040, 1.1030, 1.1035)]
        _, broker, clock, _ = run(rows)
        first_minute(clock)
        broker.send(pending(C["ORDER_TYPE_BUY_STOP"], 1.1015))
        clock.sleep(120)
        assert broker.positions[0].price_open == 1.1015

    def test_price_on_the_wrong_side_is_rejected(self):
        _, broker, clock, _ = run([FLAT] * 2)
        first_minute(clock)
        outcome = broker.send(pending(C["ORDER_TYPE_BUY_LIMIT"], 1.1005))
        assert outcome.retcode == C["TRADE_RETCODE_INVALID_PRICE"]

    def test_expiry_and_removal(self):
        _, broker, clock, _ = run([FLAT] * 5)
        first_minute(clock)
        expiry = datetime(2025, 1, 6, 10, 2)
        broker.send(
            pending(
                C["ORDER_TYPE_SELL_LIMIT"],
                1.1050,
                type_time=C["ORDER_TIME_SPECIFIED"],
                expiration=expiry,
            )
        )
        removed = broker.send(pending(C["ORDER_TYPE_SELL_LIMIT"], 1.1060))
        broker.send({"action": C["TRADE_ACTION_REMOVE"], "order": removed.order})
        clock.sleep(240)
        states = [o.state for o in broker.history_orders]
        assert states == [C["ORDER_STATE_CANCELED"], C["ORDER_STATE_EXPIRED"]]

    def test_modify_position_levels(self):
        _, broker, clock, _ = run([FLAT] * 3)
        first_minute(clock)
        opened = broker.send(market_order(BUY))
        changed = broker.send(
            {"action": C["TRADE_ACTION_SLTP"], "position": opened.order, "sl": 1.0950}
        )
        assert changed.retcode == C["TRADE_RETCODE_DONE"]
        assert broker.positions[0].sl == 1.0950


class TestSwap:
    def hold(self, start, days):
        rows = [FLAT] * (days * 1440 + 1)
        _, broker, clock, _ = run(rows, start=start)
        first_minute(clock)
        opened = broker.send(market_order(BUY))
        clock.sleep(days * 86_400)
        broker.send(market_order(SELL, position=opened.order))
        return broker.deals[-1].swap

    def test_tuesday_and_wednesday_nights(self):
        # Tuesday night 1x, Wednesday night 3x: four days of -7.60 per lot.
        assert self.hold(datetime(2025, 1, 7, 10, 0), 2) == pytest.approx(-30.40)

    def test_friday_night_only_over_a_weekend(self):
        assert self.hold(datetime(2025, 1, 10, 10, 0), 3) == pytest.approx(-7.60)


class TestOtherModesAndCurrencies:
    def test_netting_reduces_and_reverses(self):
        rows = [FLAT] * 4
        _, broker, clock, _ = run(rows, margin_mode=C["ACCOUNT_MARGIN_MODE_RETAIL_NETTING"])
        first_minute(clock)
        broker.send(market_order(BUY, 1.0))
        broker.send(market_order(SELL, 0.4))
        assert broker.positions[0].volume == pytest.approx(0.6)
        broker.send(market_order(SELL, 1.0))
        entries = [d.entry for d in broker.deals[1:]]
        assert entries == [C["DEAL_ENTRY_IN"], C["DEAL_ENTRY_OUT"], C["DEAL_ENTRY_INOUT"]]
        assert broker.positions[0].type == SELL
        assert broker.positions[0].volume == pytest.approx(0.4)

    def test_profit_in_the_quote_currency_is_converted_at_the_closing_price(self):
        usdjpy = spec(
            name="USDJPY@",
            digits=3,
            point=0.001,
            trade_tick_size=0.001,
            currency_base="USD",
            currency_profit="JPY",
            currency_margin="USD",
        )
        rows = [(150.0, 150.0, 150.0, 150.0), (151.5, 151.5, 151.5, 151.5)]
        m1 = bars(START, rows)
        from simdata import simulation as make

        _, broker, clock, _ = make(
            m1, ms(2025, 1, 6, 10), ms(2025, 1, 6, 10, 5), symbol_spec=usdjpy
        )
        clock.sleep(0)
        opened = broker.send({**market_order(BUY, 1.0), "symbol": "USDJPY@"})
        clock.sleep(60)
        broker.send({**market_order(SELL, 1.0), "symbol": "USDJPY@", "position": opened.order})
        expected = (151.5 - opened.price) * 100_000 / 151.5
        assert broker.deals[-1].profit == pytest.approx(round(expected, 2))


def test_end_of_test_closes_everything():
    _, broker, clock, _ = run([FLAT] * 3, minutes_after=1)
    first_minute(clock)
    broker.send(market_order(BUY))
    broker.send(pending(C["ORDER_TYPE_BUY_LIMIT"], 1.0900))
    from strategylab.sim.clock import SimulationFinished

    with pytest.raises(SimulationFinished):
        clock.sleep(3600)
    assert broker.positions == [] and broker.orders == []
    assert broker.deals[-1].comment == "end of test"
    assert broker.history_orders[-1].state in (C["ORDER_STATE_CANCELED"], C["ORDER_STATE_FILLED"])


def test_flat_bars_helper_is_flat():
    assert set(flat_bars(START, 3)["high"]) == {1.1}

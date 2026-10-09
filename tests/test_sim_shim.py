import sys
from datetime import datetime

import numpy as np
import pytest
from simdata import bars, ms, simulation

from strategylab.sim import clock as clock_module
from strategylab.sim import shim
from strategylab.sim.broker import SimulatorUnsupportedError
from strategylab.sim.clock import SimulationFinished, SimulationStalledError
from strategylab.sim.constants import CONSTANTS, RECORD_FIELDS
from strategylab.sim.market import RATE_DTYPE, TICK_DTYPE

FLAT = (1.1000, 1.1000, 1.1000, 1.1000)


@pytest.fixture
def mt5():
    m1 = bars(datetime(2025, 1, 6, 10, 0), [FLAT] * 120)
    sim = simulation(m1, ms(2025, 1, 6, 10), ms(2025, 1, 6, 12))
    previous = sys.modules.get("MetaTrader5")
    module = shim.install(sim[3])
    module.clock = sim[2]
    yield module
    if previous is None:
        sys.modules.pop("MetaTrader5", None)
    else:
        sys.modules["MetaTrader5"] = previous
    shim._terminal = None


def test_matches_the_real_package():
    real = pytest.importorskip("MetaTrader5")
    for name, value in CONSTANTS.items():
        assert getattr(real, name) == value, name
    for record, fields in RECORD_FIELDS.items():
        assert getattr(real, record).__match_args__ == fields, record


def test_installed_under_the_real_name(mt5):
    import MetaTrader5

    assert MetaTrader5 is mt5
    assert MetaTrader5.TIMEFRAME_H1 == 16385
    assert MetaTrader5.initialize() is True


def test_unsupported_functions_name_themselves(mt5):
    with pytest.raises(SimulatorUnsupportedError, match=r"MetaTrader5\.market_book_add"):
        mt5.market_book_add("EURUSD@")


def test_broker_suffix_is_optional_and_other_symbols_are_refused(mt5):
    assert mt5.symbol_select("EURUSD") is True
    assert mt5.symbol_info("eurusd").name == "EURUSD@"
    with pytest.raises(SimulatorUnsupportedError, match=r"copy_rates_from_pos: only EURUSD@"):
        mt5.copy_rates_from_pos("GBPUSD", mt5.TIMEFRAME_H1, 0, 10)


def test_rates_and_ticks_have_the_package_layout(mt5):
    mt5.clock.sleep(600)
    rates = mt5.copy_rates_from_pos("EURUSD", mt5.TIMEFRAME_M1, 0, 5)
    ticks = mt5.copy_ticks_from("EURUSD", datetime(2025, 1, 6, 10), 10, mt5.COPY_TICKS_ALL)
    assert rates.dtype == RATE_DTYPE and len(rates) == 5
    assert ticks.dtype == TICK_DTYPE and len(ticks) == 10
    assert isinstance(rates, np.ndarray)


def test_bad_timeframe_answers_none_with_an_error(mt5):
    assert mt5.copy_rates_from_pos("EURUSD", 7, 0, 5) is None
    assert mt5.last_error() == (CONSTANTS["RES_E_INVALID_PARAMS"], "Invalid params")


def test_trading_round_trip(mt5):
    mt5.clock.sleep(0)
    result = mt5.order_send(
        {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": "EURUSD",
            "type": mt5.ORDER_TYPE_BUY,
            "volume": 0.1,
            "type_filling": mt5.ORDER_FILLING_FOK,
            "comment": "test",
        }
    )
    assert result.retcode == mt5.TRADE_RETCODE_DONE
    assert result.request.symbol == "EURUSD@"
    assert result._fields == RECORD_FIELDS["OrderSendResult"]
    (position,) = mt5.positions_get(symbol="EURUSD")
    assert (position.volume, position.price_open, position.comment) == (0.1, 1.1001, "test")
    assert mt5.positions_get(group="*GBP*") == ()
    deals = mt5.history_deals_get(datetime(2025, 1, 1), datetime(2025, 2, 1))
    assert [d.type for d in deals] == [mt5.DEAL_TYPE_BALANCE, mt5.DEAL_TYPE_BUY]
    assert mt5.history_deals_get(position=position.ticket)[0].entry == mt5.DEAL_ENTRY_IN
    account = mt5.account_info()
    assert account.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO
    assert account.balance == 10_000.0


def test_unknown_request_fields_are_refused(mt5):
    mt5.clock.sleep(0)
    with pytest.raises(SimulatorUnsupportedError, match="unknown request field"):
        mt5.order_send({"action": mt5.TRADE_ACTION_DEAL, "lots": 1})


def test_order_check(mt5):
    mt5.clock.sleep(0)
    check = mt5.order_check(
        {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": "EURUSD",
            "type": mt5.ORDER_TYPE_BUY,
            "volume": 1.0,
        }
    )
    assert check.retcode == 0
    assert check.margin == pytest.approx(1100.1)
    assert mt5.positions_total() == 0


class TestClock:
    def test_idle_time_is_skipped_to_the_next_price(self, mt5):
        clock = mt5.clock
        clock.sleep(0)
        assert clock.now_ms == ms(2025, 1, 6, 10)
        clock.sleep(1)
        assert clock.now_ms == ms(2025, 1, 6, 10, 0, 20)

    def test_a_long_sleep_moves_exactly_that_far(self, mt5):
        mt5.clock.sleep(0)
        mt5.clock.sleep(1800)
        assert mt5.clock.now_ms == ms(2025, 1, 6, 10, 30)

    def test_the_end_raises_and_stays_raised(self, mt5):
        with pytest.raises(SimulationFinished):
            mt5.clock.sleep(10 * 3600)
        assert mt5.clock.now_ms == ms(2025, 1, 6, 12)
        with pytest.raises(SimulationFinished):
            mt5.account_info()
        assert issubclass(SimulationFinished, BaseException)
        assert not issubclass(SimulationFinished, Exception)

    def test_a_loop_that_never_sleeps_is_stopped(self, mt5, monkeypatch):
        monkeypatch.setattr(clock_module, "MAX_CALLS_WITHOUT_SLEEP", 50)
        with pytest.raises(SimulationStalledError, match="without sleeping"):
            for _ in range(100):
                mt5.symbol_info_tick("EURUSD")

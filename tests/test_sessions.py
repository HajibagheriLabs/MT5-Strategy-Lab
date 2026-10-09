from datetime import datetime

import pytest
from simdata import bars, ms, simulation

from strategylab.sessions import SessionError, Sessions, parse_session_log
from strategylab.sim.constants import CONSTANTS as C

# What SessionExport.mq5 printed for EURUSD on the demo server: quotes all day, trading from
# 00:05 on Monday and 00:03 on other weekdays, until 23:59.
PREFIX = "CS\t0\t06:41:01\tSessionExport (EURUSD@,H1)\t2025.06.02 00:00:00   "
LOG = "\n".join(
    PREFIX + line
    for line in [
        "SESSION QUOTE 1 0 86400",
        "SESSION TRADE 1 300 86340",
        *[f"SESSION TRADE {day} 180 86340" for day in (2, 3, 4, 5)],
        "SESSION END",
    ]
)
FLAT = (1.1, 1.1, 1.1, 1.1)


def test_parse_and_round_trip():
    sessions = parse_session_log(LOG)
    assert sessions.trade[1] == [(300, 86340)]
    assert sessions.trade[3] == [(180, 86340)]
    assert sessions.trade[0] == []
    assert sessions.quote[1] == [(0, 86400)]
    assert Sessions.from_json(sessions.to_json()) == sessions


def test_nothing_printed():
    with pytest.raises(SessionError, match="printed nothing"):
        parse_session_log("no sessions here")


def tuesday_from_midnight(minutes: int):
    m1 = bars(datetime(2025, 1, 7, 0, 0), [FLAT] * minutes)
    sim = simulation(m1, ms(2025, 1, 7), ms(2025, 1, 7, 0, minutes + 5))
    broker = sim[1]
    broker.trade_sessions = parse_session_log(LOG).trade
    broker._tradable = broker._session_mask()
    return sim


def test_orders_before_the_session_opens_are_refused():
    _, broker, clock, _ = tuesday_from_midnight(10)
    clock.sleep(0)
    request = {
        "action": C["TRADE_ACTION_DEAL"],
        "type": C["ORDER_TYPE_BUY"],
        "volume": 0.1,
        "type_filling": C["ORDER_FILLING_FOK"],
    }
    assert broker.send(request).retcode == C["TRADE_RETCODE_MARKET_CLOSED"]
    clock.sleep(180)  # 00:03, Tuesday's session has opened
    assert broker.send(request).retcode == C["TRADE_RETCODE_DONE"]


def test_stops_wait_for_the_session():
    rows = [FLAT] * 6
    rows[1] = (1.0950, 1.0950, 1.0950, 1.0950)  # 00:01: through the stop, but trading is closed
    rows[2] = (1.0960, 1.0960, 1.0960, 1.0960)
    rows[3] = (1.0960, 1.0960, 1.0960, 1.0960)  # 00:03: the session opens, still below the stop
    m1 = bars(datetime(2025, 1, 7, 0, 0), rows)
    _, broker, clock, _ = simulation(m1, ms(2025, 1, 6, 23, 50), ms(2025, 1, 7, 0, 10))
    broker.trade_sessions = parse_session_log(LOG).trade
    broker._tradable = broker._session_mask()
    from strategylab.sim.broker import Position

    broker.positions.append(Position(9, C["ORDER_TYPE_BUY"], 0.1, 1.1, 1.0990, 0.0, 0, 0, 0, "", 0))
    clock.sleep(13 * 60)  # to 00:03
    deal = broker.deals[-1]
    assert deal.time_ms == ms(2025, 1, 7, 0, 3)
    assert (deal.price, deal.comment) == (1.0990, "sl 1.09900")


def test_without_sessions_everything_is_tradable_and_said_so():
    m1 = bars(datetime(2025, 1, 7, 0, 0), [FLAT] * 3)
    _, broker, _, _ = simulation(m1, ms(2025, 1, 7), ms(2025, 1, 7, 0, 5))
    assert broker._tradable.all()
    assert any("trading sessions were not known" in note for note in broker.notes)


@pytest.mark.mt5
def test_sessions_come_from_the_terminal(tmp_path):
    from strategylab.config import load_settings
    from strategylab.sessions import export_sessions

    settings = load_settings()
    symbols = [
        p.name
        for server in settings.terminal.history_servers()
        for p in (settings.terminal.data_dir / "bases" / server / "history").iterdir()
        if p.name.upper().startswith("EURUSD")
    ]
    if not symbols:
        pytest.skip("no EURUSD history in the terminal yet")
    path = export_sessions(settings.terminal, symbols[0], tmp_path, tmp_path / "runs")
    sessions = Sessions.from_json(path.read_text(encoding="utf-8"))
    weekdays = [sessions.trade[day] for day in range(1, 6)]
    assert all(weekdays), "every weekday should have a trade session"
    for spans in (*sessions.trade.values(), *sessions.quote.values()):
        assert all(0 <= start < end <= 86_400 for start, end in spans)

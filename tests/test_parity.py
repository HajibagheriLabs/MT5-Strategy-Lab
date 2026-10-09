from datetime import date, datetime, timedelta

import pytest

from strategylab.parity import compare, exit_kind, pair, trades
from strategylab.result import BacktestResult, Deal, Engine, RunMeta, TickModel

POINT = 0.00001
T0 = datetime(2025, 1, 6, 10, 0)


def deal(ticket, minutes, type_, entry, price, profit=0.0, comment="", swap=0.0):
    return Deal(
        ticket=ticket,
        server_time=T0 + timedelta(minutes=minutes),
        time=None,
        symbol="EURUSD@",
        type=type_,
        entry=entry,
        volume=0.1,
        price=price,
        order=ticket,
        commission=0.0,
        swap=swap,
        profit=profit,
        balance=None,
        comment=comment,
    )


def result(deals):
    meta = RunMeta(
        engine=Engine.MT5_TESTER,
        fidelity="",
        strategy_name="parity",
        strategy_hash=None,
        symbol="EURUSD@",
        timeframe="H1",
        date_from=date(2025, 1, 1),
        date_to=date(2025, 2, 1),
        model=TickModel.OHLC_M1,
        deposit=10_000,
        currency="USD",
        leverage=100,
        parameters={},
    )
    balance = Deal(
        ticket=1, server_time=T0, time=None, symbol=None, type="balance", entry=None,
        volume=None, price=None, order=None, commission=0, swap=0, profit=10_000, balance=10_000,
    )  # fmt: skip
    return BacktestResult(meta=meta, deals=[balance, *deals], balance=[])


TESTER = [
    deal(2, 0, "buy", "in", 1.10010),
    deal(3, 30, "sell", "out", 1.09810, -20.0, "sl 1.09810"),
    deal(4, 120, "sell", "in", 1.10000),
    deal(5, 200, "buy", "out", 1.09600, 40.0, "tp 1.09600", swap=0.16),
]


def test_trades_pair_entries_with_exits():
    found = trades(result(TESTER))
    assert [(t.direction, t.exit_kind, t.level, t.result) for t in found] == [
        ("buy", "sl", 1.09810, -20.0),
        ("sell", "tp", 1.09600, 40.16),
    ]


@pytest.mark.parametrize(
    ("comment", "expected"),
    [
        ("sl 1.09810", ("sl", 1.09810)),
        ("tp 1.2", ("tp", 1.2)),
        ("end of test", ("end", None)),
        ("parity", ("strategy", None)),
    ],
)
def test_exit_kinds(comment, expected):
    assert exit_kind(comment) == expected


def test_identical_runs():
    comparison = compare(result(TESTER), result(TESTER), POINT)
    assert (comparison.matched, comparison.identical) == (2, 2)
    assert comparison.identical_rate == 1.0
    assert comparison.first_difference is None
    assert comparison.net_profit == (20.16, 20.16)


def test_a_different_exit_and_an_extra_trade():
    candidate = [
        deal(2, 0, "buy", "in", 1.10012),  # two points more spread
        deal(3, 31, "sell", "out", 1.09810, -20.2, "sl 1.09810"),
        deal(4, 60, "buy", "in", 1.10100),  # a trade the tester never took
        deal(5, 70, "sell", "out", 1.10500, 40.0, "tp 1.10500"),
        deal(6, 120, "sell", "in", 1.10000),
        deal(7, 200, "buy", "out", 1.09600, 40.0, "tp 1.09600", swap=0.16),
    ]
    comparison = compare(result(TESTER), result(candidate), POINT)
    assert (comparison.reference_trades, comparison.candidate_trades) == (2, 3)
    assert (comparison.matched, comparison.identical) == (2, 1)
    assert (comparison.only_reference, comparison.only_candidate) == (0, 1)
    assert comparison.entry_points_buys["max_abs"] == pytest.approx(2.0)
    assert comparison.entry_points_sells["zero_share"] == 1.0
    assert comparison.same_exit == 1
    assert (comparison.same_exit_kind, comparison.exit_within_minute) == (2, 2)
    tester_trade, simulated = comparison.first_difference
    assert tester_trade.entry_time == simulated.entry_time == T0


def test_the_net_difference_is_split_by_cause():
    candidate = [
        deal(2, 0, "buy", "in", 1.10012),  # two points dearer: 0.20 at 0.10 per point
        deal(3, 31, "sell", "out", 1.09809, -20.3, "sl 1.09810"),  # one point worse
        deal(4, 60, "buy", "in", 1.10100),  # a trade the tester never took
        deal(5, 70, "sell", "out", 1.10500, 40.0, "tp 1.10500"),
        deal(6, 120, "sell", "in", 1.10000),
        deal(7, 140, "buy", "out", 1.10200, -20.0, "sl 1.10200"),  # stopped, not a take profit
    ]
    comparison = compare(result(TESTER), result(candidate), POINT)
    parts = comparison.net_difference
    assert parts["entry_price"] == pytest.approx(-0.2)
    assert parts["exit_price"] == pytest.approx(-0.1)
    assert parts["unpaired"] == pytest.approx(40.0)
    assert parts["other_exit"] == pytest.approx(-60.16)
    assert parts["other"] == pytest.approx(0.0)
    total = comparison.net_profit[1] - comparison.net_profit[0]
    assert sum(parts.values()) == pytest.approx(total)


def test_pairing_needs_the_same_direction_within_a_minute():
    a = trades(result(TESTER))
    shifted = [
        t.__class__(**{**t.__dict__, "entry_time": t.entry_time + timedelta(minutes=2)}) for t in a
    ]
    assert all(x is None or y is None for x, y in pair(a, shifted))
    flipped = [t.__class__(**{**t.__dict__, "direction": "sell"}) for t in a[:1]]
    assert pair(a[:1], flipped) == [(a[0], None), (None, flipped[0])]

import math
from datetime import datetime
from itertools import pairwise

import pytest

from strategylab.metrics import (
    DEFINITIONS,
    METATRADER_EQUIVALENTS,
    balance_path,
    compute_metrics,
    trades_from_deals,
)
from strategylab.result import Deal


def deal(ticket, day, type_, entry, profit=0.0, swap=0.0, commission=0.0):
    return Deal(
        ticket=ticket,
        server_time=datetime(2025, 1, day, 12),
        time=None,
        symbol=None if type_ == "balance" else "EURUSD",
        type=type_,
        entry=entry,
        volume=None if type_ == "balance" else 1.0,
        price=None,
        order=None,
        commission=commission,
        swap=swap,
        profit=profit,
        balance=None,
    )


# A deposit of 1000 and five trades, each paying 1 commission on entry and 1 on exit:
#   long  +100 profit, -2 swap  -> result  97      balance 999 -> 1096
#   short  -50                  -> result -51      balance 1095 -> 1044
#   long   -30 profit, -1 swap  -> result -32      balance 1043 -> 1011
#   long   +60                  -> result  59      balance 1010 -> 1069
#   long   +40                  -> result  39      balance 1068 -> 1107
DEALS = [
    deal(1, 1, "balance", None, profit=1000),
    deal(2, 2, "buy", "in", commission=-1),
    deal(3, 2, "sell", "out", profit=100, swap=-2, commission=-1),
    deal(4, 3, "sell", "in", commission=-1),
    deal(5, 3, "buy", "out", profit=-50, commission=-1),
    deal(6, 6, "buy", "in", commission=-1),
    deal(7, 6, "sell", "out", profit=-30, swap=-1, commission=-1),
    deal(8, 7, "buy", "in", commission=-1),
    deal(9, 7, "sell", "out", profit=60, commission=-1),
    deal(10, 8, "buy", "in", commission=-1),
    deal(11, 8, "sell", "out", profit=40, commission=-1),
]


@pytest.fixture(scope="module")
def metrics():
    return compute_metrics(DEALS)


def test_balance_path():
    assert balance_path(DEALS) == [1000, 999, 1096, 1095, 1044, 1043, 1011, 1010, 1069, 1068, 1107]


def test_trades():
    trades = trades_from_deals(DEALS)
    assert [t.result for t in trades] == [97, -51, -32, 59, 39]
    assert [t.side for t in trades] == ["long", "short", "long", "long", "long"]
    assert (trades[0].balance_before, trades[0].balance_after) == (999, 1096)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("net_profit", 107.0),  # 112 from trade results, minus 5 entry commissions
        ("gross_profit", 195.0),
        ("gross_loss", -83.0),
        ("profit_factor", 195 / 83),
        ("expected_payoff", 112 / 5),
        ("trades", 5),
        ("total_deals", 10),
        ("wins", 3),
        ("losses", 2),
        ("win_rate_pct", 60.0),
        ("long_trades", 4),
        ("long_win_rate_pct", 75.0),
        ("short_trades", 1),
        ("short_win_rate_pct", 0.0),
        ("average_win", 65.0),
        ("average_loss", -41.5),
        ("largest_win", 97.0),
        ("largest_loss", -51.0),
        ("max_consecutive_wins", 2),
        ("max_consecutive_wins_money", 98.0),
        ("max_consecutive_losses", 2),
        ("max_consecutive_losses_money", -83.0),
        ("max_consecutive_profit", 98.0),
        ("max_consecutive_profit_count", 2),
        ("max_consecutive_loss", -83.0),
        ("max_consecutive_loss_count", 2),
        ("average_consecutive_wins", 1.5),
        ("average_consecutive_losses", 2.0),
        ("initial_deposit", 1000.0),
        ("balance_dd_absolute", 1.0),  # the first entry commission takes it to 999
        ("balance_dd_maximal", 86.0),  # from 1096 down to 1010
        ("balance_dd_maximal_pct", 86 / 1096 * 100),
        ("balance_dd_relative_pct", 86 / 1096 * 100),
        ("balance_dd_relative", 86.0),
        ("recovery_factor", 107 / 86),
    ],
)
def test_hand_computed_metric(metrics, name, expected):
    assert metrics[name] == pytest.approx(expected)


def test_holding_period_returns(metrics):
    ratios = [1096 / 999, 1044 / 1095, 1011 / 1043, 1069 / 1010, 1107 / 1068]
    assert metrics["ahpr"] == pytest.approx(sum(ratios) / 5)
    assert metrics["ghpr"] == pytest.approx(math.prod(ratios) ** (1 / 5))


def test_sharpe_uses_weekday_closing_balances(metrics):
    # 1-8 January 2025 runs Wednesday to Wednesday; the weekend is skipped.
    closes = [1000, 1096, 1044, 1011, 1069, 1107]
    returns = [b / a - 1 for a, b in pairwise(closes)]
    mean = sum(returns) / len(returns)
    sd = math.sqrt(sum((r - mean) ** 2 for r in returns) / (len(returns) - 1))
    assert metrics["sharpe_ratio"] == pytest.approx(mean / sd * math.sqrt(252))


def results_deals(results):
    deals = [deal(1, 1, "balance", None, profit=10_000)]
    for index, result in enumerate(results):
        deals.append(deal(2 * index + 2, 2, "buy", "in"))
        deals.append(deal(2 * index + 3, 2, "sell", "out", profit=result))
    return deals


def test_ties_between_longest_runs_pick_the_most_extreme():
    # Three winning runs of two trades: 6, then 33, then 4. MetaTrader reports the 33 run,
    # which is neither the first nor the last.
    results = [5, 1, -1, 3, 30, -2, 2, 2, -40, -1, 7, -3, -4]
    metrics = compute_metrics(results_deals(results))
    assert metrics["max_consecutive_wins"] == 2
    assert metrics["max_consecutive_wins_money"] == 33
    assert metrics["max_consecutive_losses"] == 2
    assert metrics["max_consecutive_losses_money"] == -41


def test_a_zero_result_ends_a_run():
    metrics = compute_metrics(results_deals([5, 0, 5, 5, -1]))
    assert metrics["max_consecutive_wins"] == 2
    assert (metrics["wins"], metrics["losses"], metrics["trades"]) == (3, 1, 5)


def test_no_trades_leaves_ratios_empty():
    metrics = compute_metrics([deal(1, 1, "balance", None, profit=500)])
    assert metrics["trades"] == 0
    assert metrics["net_profit"] == 0.0
    for name in ("profit_factor", "expected_payoff", "win_rate_pct", "recovery_factor"):
        assert metrics[name] is None
    assert metrics["sharpe_ratio"] is None


def test_no_losing_trade_means_no_profit_factor():
    metrics = compute_metrics(results_deals([5, 10]))
    assert metrics["profit_factor"] is None
    assert metrics["balance_dd_maximal"] == 0
    assert metrics["recovery_factor"] is None


def test_wiped_out_account_has_no_geometric_mean():
    metrics = compute_metrics(results_deals([-10_000]))
    assert metrics["ghpr"] is None
    assert metrics["balance_dd_relative_pct"] == pytest.approx(100.0)


def test_every_metric_is_defined_and_mapped():
    computed = set(compute_metrics(DEALS))
    assert computed == set(DEFINITIONS)
    assert set(METATRADER_EQUIVALENTS) <= computed

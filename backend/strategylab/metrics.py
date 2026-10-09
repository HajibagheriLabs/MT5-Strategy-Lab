"""Backtest metrics computed from the deals table alone.

Both engines produce a deals table, so figures computed from it mean the same thing for a Strategy
Tester run and a simulated run. Definitions follow MetaTrader's testing report wherever a deals
table allows; each is stated in DEFINITIONS. Where MetaTrader uses data a deals table does not
carry (the equity curve), the figure here is a different, stated measure and
reports/metric_crosscheck.md shows by how much the two differ.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from itertools import pairwise
from typing import Literal

from strategylab.result import Deal

TRADING_DAYS_PER_YEAR = 252
# Deposits, withdrawals and credit move capital in or out; they are not trading results.
CAPITAL_TYPES = {"balance", "credit"}

DEFINITIONS: dict[str, str] = {
    "net_profit": (
        "Sum of profit, swap and commission over every deal except capital movements (balance "
        "and credit deals). Commission charged on entry deals is included."
    ),
    "gross_profit": "Sum of the results of winning trades.",
    "gross_loss": "Sum of the results of losing trades (a negative number).",
    "profit_factor": "gross_profit / |gross_loss|; empty when there is no losing trade.",
    "expected_payoff": "Sum of trade results / number of trades.",
    "trades": (
        "Number of deals that close a position (direction out, in/out or out by). A trade's "
        "result is that deal's profit + swap + commission."
    ),
    "total_deals": "Number of deals, capital movements excluded.",
    "wins": "Trades with a result above zero.",
    "losses": "Trades with a result below zero. A result of exactly zero is neither.",
    "win_rate_pct": "wins / trades * 100.",
    "long_trades": "Trades closing a long position (the closing deal is a sell).",
    "long_win_rate_pct": "Winning long trades / long trades * 100.",
    "short_trades": "Trades closing a short position (the closing deal is a buy).",
    "short_win_rate_pct": "Winning short trades / short trades * 100.",
    "average_win": "gross_profit / wins.",
    "average_loss": "gross_loss / losses.",
    "largest_win": "Largest result of a winning trade.",
    "largest_loss": "Smallest (most negative) result of a losing trade.",
    "max_consecutive_wins": (
        "Length of the longest run of winning trades; a losing or zero trade ends a run. Among "
        "equally long runs, the one with the largest total profit is used, as MetaTrader does."
    ),
    "max_consecutive_wins_money": "Sum of results in that longest winning run.",
    "max_consecutive_losses": (
        "Length of the longest run of losing trades. Among equally long runs, the one with the "
        "largest total loss is used, as MetaTrader does."
    ),
    "max_consecutive_losses_money": "Sum of results in that longest losing run.",
    "max_consecutive_profit": "Largest sum of results over any single winning run.",
    "max_consecutive_profit_count": "Number of trades in that run.",
    "max_consecutive_loss": "Most negative sum of results over any single losing run.",
    "max_consecutive_loss_count": "Number of trades in that run.",
    "average_consecutive_wins": "Mean length of winning runs.",
    "average_consecutive_losses": "Mean length of losing runs.",
    "initial_deposit": "Sum of balance deals before the first trade.",
    "balance_dd_absolute": (
        "initial_deposit minus the lowest balance reached below it; 0 if the balance never fell "
        "below the deposit."
    ),
    "balance_dd_maximal": (
        "Largest fall of the balance from its running high, in money. The balance is the deposit "
        "plus every deal's profit, swap and commission, taken after each deal."
    ),
    "balance_dd_maximal_pct": "That fall as a percentage of the high it fell from.",
    "balance_dd_relative_pct": "Largest fall of the balance from its running high, in percent.",
    "balance_dd_relative": "That fall in money.",
    "recovery_factor": "net_profit / balance_dd_maximal; empty when there was no drawdown.",
    "sharpe_ratio": (
        "Annualised Sharpe ratio of daily balance returns, risk-free rate 0: the balance at the "
        "end of each weekday (server date) from the first deal to the last, simple returns "
        "between consecutive weekdays, mean / sample standard deviation * sqrt(252)."
    ),
    "ahpr": (
        "Arithmetic mean over trades of balance after / balance before the closing deal "
        "(holding period return)."
    ),
    "ghpr": "Geometric mean of the same ratios.",
}


@dataclass(frozen=True)
class Trade:
    server_date: date
    side: Literal["long", "short"]
    result: float
    balance_before: float
    balance_after: float


def _money(deal: Deal) -> float:
    return deal.profit + deal.swap + deal.commission


def balance_path(deals: Sequence[Deal]) -> list[float]:
    """Balance after each deal, summed from the deals' own money columns, to the cent."""
    balance = 0.0
    path = []
    for deal in deals:
        balance = round(balance + _money(deal), 2)
        path.append(balance)
    return path


def trades_from_deals(deals: Sequence[Deal]) -> list[Trade]:
    trades = []
    path = balance_path(deals)
    for index, deal in enumerate(deals):
        if not deal.closes_position:
            continue
        before = path[index - 1] if index else 0.0
        trades.append(
            Trade(
                server_date=deal.server_time.date(),
                side="long" if deal.type == "sell" else "short",
                result=round(_money(deal), 2),
                balance_before=before,
                balance_after=path[index],
            )
        )
    return trades


def _runs(results: Iterable[float]) -> tuple[list[list[float]], list[list[float]]]:
    """Consecutive winning runs and losing runs; a zero result ends a run without starting one."""
    wins: list[list[float]] = []
    losses: list[list[float]] = []
    current: list[float] = []
    current_sign = 0
    for result in results:
        sign = (result > 0) - (result < 0)
        if sign != current_sign and current:
            (wins if current_sign > 0 else losses).append(current)
            current = []
        current_sign = sign
        if sign:
            current.append(result)
    if current:
        (wins if current_sign > 0 else losses).append(current)
    return wins, losses


def _pct(part: int, whole: int) -> float | None:
    return part / whole * 100 if whole else None


def drawdowns(deposit: float, path: Sequence[float]) -> dict[str, float]:
    peak = deposit
    lowest = deposit
    maximal = maximal_pct = relative_pct = relative = 0.0
    for balance in path:
        if balance > peak:
            peak = balance
        lowest = min(lowest, balance)
        fall = peak - balance
        if fall > maximal:
            maximal = fall
            maximal_pct = fall / peak * 100 if peak > 0 else 0.0
        fall_pct = fall / peak * 100 if peak > 0 else 0.0
        if fall_pct > relative_pct:
            relative_pct = fall_pct
            relative = fall
    return {
        "balance_dd_absolute": max(0.0, deposit - lowest),
        "balance_dd_maximal": maximal,
        "balance_dd_maximal_pct": maximal_pct,
        "balance_dd_relative_pct": relative_pct,
        "balance_dd_relative": relative,
    }


def sharpe_ratio(deals: Sequence[Deal], path: Sequence[float]) -> float | None:
    if not deals:
        return None
    closing: dict[date, float] = {}
    for deal, balance in zip(deals, path, strict=True):
        closing[deal.server_time.date()] = balance
    first, last = min(closing), max(closing)
    series: list[float] = []
    balance = closing[first]
    day = first
    while day <= last:
        balance = closing.get(day, balance)
        if day.weekday() < 5:
            series.append(balance)
        day += timedelta(days=1)
    returns = [b / a - 1 for a, b in pairwise(series) if a > 0]
    if len(returns) < 2:
        return None
    spread = statistics.stdev(returns)
    if spread == 0:
        return None
    return statistics.mean(returns) / spread * math.sqrt(TRADING_DAYS_PER_YEAR)


def compute_metrics(deals: Sequence[Deal]) -> dict[str, float | int | None]:
    """Every metric in DEFINITIONS, from the deals of one run in time order."""
    deals = list(deals)
    path = balance_path(deals)
    trades = trades_from_deals(deals)
    results = [t.result for t in trades]
    first_trade = next((i for i, d in enumerate(deals) if d.type not in CAPITAL_TYPES), len(deals))
    deposit = round(sum(d.profit for d in deals[:first_trade] if d.type == "balance"), 2)

    winning = [r for r in results if r > 0]
    losing = [r for r in results if r < 0]
    gross_profit = round(float(sum(winning)), 2)
    gross_loss = round(float(sum(losing)), 2)
    net_profit = round(float(sum(_money(d) for d in deals if d.type not in CAPITAL_TYPES)), 2)
    win_runs, loss_runs = _runs(results)
    longest_win = max(win_runs, key=lambda run: (len(run), sum(run)), default=[])
    longest_loss = max(loss_runs, key=lambda run: (len(run), -sum(run)), default=[])
    best_run = max(win_runs, key=sum, default=[])
    worst_run = min(loss_runs, key=sum, default=[])
    longs = [t for t in trades if t.side == "long"]
    shorts = [t for t in trades if t.side == "short"]
    ratios = [t.balance_after / t.balance_before for t in trades if t.balance_before > 0]
    dd = drawdowns(deposit, path[first_trade:])

    metrics: dict[str, float | int | None] = {
        "net_profit": net_profit,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": gross_profit / -gross_loss if gross_loss else None,
        "expected_payoff": sum(results) / len(results) if results else None,
        "trades": len(trades),
        "total_deals": sum(1 for d in deals if d.type not in CAPITAL_TYPES),
        "wins": len(winning),
        "losses": len(losing),
        "win_rate_pct": _pct(len(winning), len(trades)),
        "long_trades": len(longs),
        "long_win_rate_pct": _pct(sum(1 for t in longs if t.result > 0), len(longs)),
        "short_trades": len(shorts),
        "short_win_rate_pct": _pct(sum(1 for t in shorts if t.result > 0), len(shorts)),
        "average_win": gross_profit / len(winning) if winning else None,
        "average_loss": gross_loss / len(losing) if losing else None,
        "largest_win": max(winning) if winning else None,
        "largest_loss": min(losing) if losing else None,
        "max_consecutive_wins": len(longest_win),
        "max_consecutive_wins_money": round(sum(longest_win), 2),
        "max_consecutive_losses": len(longest_loss),
        "max_consecutive_losses_money": round(sum(longest_loss), 2),
        "max_consecutive_profit": round(sum(best_run), 2),
        "max_consecutive_profit_count": len(best_run),
        "max_consecutive_loss": round(sum(worst_run), 2),
        "max_consecutive_loss_count": len(worst_run),
        "average_consecutive_wins": statistics.mean(map(len, win_runs)) if win_runs else None,
        "average_consecutive_losses": statistics.mean(map(len, loss_runs)) if loss_runs else None,
        "initial_deposit": deposit,
        **dd,
        "recovery_factor": net_profit / dd["balance_dd_maximal"]
        if dd["balance_dd_maximal"]
        else None,
        "sharpe_ratio": sharpe_ratio(deals, path),
        "ahpr": statistics.mean(ratios) if ratios else None,
        # A ratio of zero or less means the account was wiped out; there is no geometric mean.
        "ghpr": (
            math.exp(statistics.mean(map(math.log, ratios))) if ratios and min(ratios) > 0 else None
        ),
    }
    return metrics


# Where each metric appears in MetaTrader's report: the label, and which figure in the cell
# (0 for the first, 1 for the one in brackets). Used to compare the two side by side.
METATRADER_EQUIVALENTS: dict[str, tuple[str, int]] = {
    "net_profit": ("Total Net Profit", 0),
    "gross_profit": ("Gross Profit", 0),
    "gross_loss": ("Gross Loss", 0),
    "profit_factor": ("Profit Factor", 0),
    "expected_payoff": ("Expected Payoff", 0),
    "trades": ("Total Trades", 0),
    "total_deals": ("Total Deals", 0),
    "wins": ("Profit Trades (% of total)", 0),
    "win_rate_pct": ("Profit Trades (% of total)", 1),
    "losses": ("Loss Trades (% of total)", 0),
    "long_trades": ("Long Trades (won %)", 0),
    "long_win_rate_pct": ("Long Trades (won %)", 1),
    "short_trades": ("Short Trades (won %)", 0),
    "short_win_rate_pct": ("Short Trades (won %)", 1),
    "average_win": ("Average profit trade", 0),
    "average_loss": ("Average loss trade", 0),
    "largest_win": ("Largest profit trade", 0),
    "largest_loss": ("Largest loss trade", 0),
    "max_consecutive_wins": ("Maximum consecutive wins ($)", 0),
    "max_consecutive_wins_money": ("Maximum consecutive wins ($)", 1),
    "max_consecutive_losses": ("Maximum consecutive losses ($)", 0),
    "max_consecutive_losses_money": ("Maximum consecutive losses ($)", 1),
    "max_consecutive_profit": ("Maximal consecutive profit (count)", 0),
    "max_consecutive_profit_count": ("Maximal consecutive profit (count)", 1),
    "max_consecutive_loss": ("Maximal consecutive loss (count)", 0),
    "max_consecutive_loss_count": ("Maximal consecutive loss (count)", 1),
    "average_consecutive_wins": ("Average consecutive wins", 0),
    "average_consecutive_losses": ("Average consecutive losses", 0),
    "balance_dd_absolute": ("Balance Drawdown Absolute", 0),
    "balance_dd_maximal": ("Balance Drawdown Maximal", 0),
    "balance_dd_maximal_pct": ("Balance Drawdown Maximal", 1),
    "balance_dd_relative_pct": ("Balance Drawdown Relative", 0),
    "balance_dd_relative": ("Balance Drawdown Relative", 1),
    "recovery_factor": ("Recovery Factor", 0),
    "sharpe_ratio": ("Sharpe Ratio", 0),
    "ahpr": ("AHPR", 0),
    "ghpr": ("GHPR", 0),
}

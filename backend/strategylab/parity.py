"""Trade-by-trade comparison of two backtests of the same one-position-at-a-time strategy.

Used by the parity study (scripts/parity.py) to measure how far the Python simulator lands from
the Strategy Tester. Trades are paired by direction and entry time; everything else is then
compared pair by pair.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from strategylab.metrics import compute_metrics
from strategylab.result import BacktestResult

ENTRY_TOLERANCE = timedelta(minutes=1)


@dataclass(frozen=True)
class Trade:
    direction: str
    entry_time: datetime
    entry_price: float
    exit_time: datetime
    exit_price: float
    result: float
    swap: float
    commission: float
    exit_kind: str
    level: float | None
    """The stop loss or take profit price that closed the trade, if one did."""


def exit_kind(comment: str) -> tuple[str, float | None]:
    head, _, value = comment.partition(" ")
    if head in ("sl", "tp"):
        try:
            return head, float(value)
        except ValueError:
            return head, None
    return ("end" if comment == "end of test" else "strategy"), None


def trades(result: BacktestResult) -> list[Trade]:
    """Pair each entry deal with the exit deal that follows it (one position at a time)."""
    found = []
    opened = None
    for deal in result.deals:
        if not deal.is_trade:
            continue
        if deal.entry == "in":
            opened = deal
        elif opened is not None:
            kind, level = exit_kind(deal.comment)
            found.append(
                Trade(
                    direction=opened.type,
                    entry_time=opened.server_time.replace(microsecond=0),
                    entry_price=opened.price or 0.0,
                    exit_time=deal.server_time.replace(microsecond=0),
                    exit_price=deal.price or 0.0,
                    result=round(deal.profit + deal.swap + deal.commission + opened.commission, 2),
                    swap=deal.swap,
                    commission=round(deal.commission + opened.commission, 2),
                    exit_kind=kind,
                    level=level,
                )
            )
            opened = None
    return found


def pair(
    reference: list[Trade], candidate: list[Trade], tolerance: timedelta = ENTRY_TOLERANCE
) -> list[tuple[Trade | None, Trade | None]]:
    """Walk both lists in time order, pairing trades of the same direction entered within
    `tolerance` of each other; anything left over is paired with None."""
    pairs: list[tuple[Trade | None, Trade | None]] = []
    i = j = 0
    while i < len(reference) or j < len(candidate):
        a = reference[i] if i < len(reference) else None
        b = candidate[j] if j < len(candidate) else None
        if a and b and a.direction == b.direction and abs(a.entry_time - b.entry_time) <= tolerance:
            pairs.append((a, b))
            i += 1
            j += 1
        elif b is None or (a is not None and a.entry_time <= b.entry_time):
            pairs.append((a, None))
            i += 1
        else:
            pairs.append((None, b))
            j += 1
    return pairs


def _distribution(values: list[float]) -> dict[str, float]:
    if not values:
        return {"count": 0}
    ordered = sorted(values)

    def quantile(q: float) -> float:
        return ordered[min(len(ordered) - 1, int(q * (len(ordered) - 1) + 0.5))]

    return {
        "count": len(values),
        "zero_share": sum(1 for v in values if abs(v) < 0.5) / len(values),
        "mean": statistics.fmean(values),
        "median": quantile(0.5),
        "p95_abs": sorted(abs(v) for v in values)[
            min(len(values) - 1, int(0.95 * (len(values) - 1) + 0.5))
        ],
        "max_abs": max(abs(v) for v in values),
    }


@dataclass
class Comparison:
    """How a candidate run differs from a reference run of the same strategy."""

    reference_trades: int
    candidate_trades: int
    matched: int
    identical: int
    only_reference: int
    only_candidate: int
    same_exit: int
    same_exit_kind: int
    exit_within_minute: int
    entry_seconds: dict[str, float]
    entry_points: dict[str, float]
    entry_points_buys: dict[str, float]
    entry_points_sells: dict[str, float]
    exit_points: dict[str, float]
    exit_slippage_reference: dict[str, float]
    exit_slippage_candidate: dict[str, float]
    result_difference: dict[str, float]
    net_profit: tuple[float, float]
    max_drawdown: tuple[float, float]
    swap: tuple[float, float]
    net_difference: dict[str, float] = field(default_factory=dict)
    """The candidate's net profit minus the reference's, split by cause (see attribute)."""
    first_difference: tuple[Trade | None, Trade | None] | None = None
    examples: list[tuple[Trade | None, Trade | None]] = field(default_factory=list)

    @property
    def match_rate(self) -> float:
        return self.matched / max(self.reference_trades, self.candidate_trades, 1)

    @property
    def identical_rate(self) -> float:
        return self.identical / max(self.reference_trades, self.candidate_trades, 1)


def _adverse_points(trade: Trade, point: float) -> float | None:
    """How much worse than its stop loss or take profit level a trade was closed, in points."""
    if trade.level is None:
        return None
    closing_sell = trade.direction == "buy"
    worse = trade.level - trade.exit_price if closing_sell else trade.exit_price - trade.level
    return worse / point


CAUSES = ("entry_price", "exit_price", "swap", "commission", "other_exit", "unpaired", "other")


def attribute(pairs: list[tuple[Trade | None, Trade | None]]) -> dict[str, float]:
    """Split the difference in net profit (candidate minus reference) by where it comes from.

    For a pair that closed the same way, the price differences are valued at the reference
    trade's money per unit of price, so a cheaper entry and a better exit are told apart. A pair
    that closed differently (a stop loss against a take profit) counts whole under other_exit,
    and a trade only one side took counts whole under unpaired. What is left (profit converted
    at a different rate, rounding to the cent) is `other`, so the parts add up to the total.
    """
    parts = dict.fromkeys(CAUSES, 0.0)
    total = 0.0
    for a, b in pairs:
        total += (b.result if b else 0.0) - (a.result if a else 0.0)
        if a is None or b is None:
            parts["unpaired"] += (b.result if b else 0.0) - (a.result if a else 0.0)
            continue
        if a.exit_kind != b.exit_kind:
            parts["other_exit"] += b.result - a.result
            continue
        sign = 1 if a.direction == "buy" else -1
        move = (a.exit_price - a.entry_price) * sign
        value = (a.result - a.swap - a.commission) / move if abs(move) > 1e-12 else 0.0
        parts["entry_price"] -= (b.entry_price - a.entry_price) * sign * value
        parts["exit_price"] += (b.exit_price - a.exit_price) * sign * value
        parts["swap"] += b.swap - a.swap
        parts["commission"] += b.commission - a.commission
    parts["other"] = total - sum(parts.values())
    return {cause: round(amount, 2) for cause, amount in parts.items()}


def identical(a: Trade, b: Trade, point: float) -> bool:
    return (
        a.direction == b.direction
        and a.entry_time == b.entry_time
        and a.exit_time == b.exit_time
        and abs(a.entry_price - b.entry_price) < point / 2
        and abs(a.exit_price - b.exit_price) < point / 2
        and abs(a.result - b.result) < 0.005
    )


def compare(reference: BacktestResult, candidate: BacktestResult, point: float) -> Comparison:
    ref, cand = trades(reference), trades(candidate)
    pairs = pair(ref, cand)
    matched = [(a, b) for a, b in pairs if a is not None and b is not None]
    differing = [(a, b) for a, b in pairs if a is None or b is None or not identical(a, b, point)]
    ref_metrics = compute_metrics(reference.deals)
    cand_metrics = compute_metrics(candidate.deals)

    def points(pairs_: list[tuple[Trade, Trade]], attribute: str) -> list[float]:
        return [(getattr(b, attribute) - getattr(a, attribute)) / point for a, b in pairs_]

    slips_ref = [_adverse_points(a, point) for a, _ in matched]
    slips_cand = [_adverse_points(b, point) for _, b in matched]
    return Comparison(
        reference_trades=len(ref),
        candidate_trades=len(cand),
        matched=len(matched),
        identical=sum(1 for a, b in matched if identical(a, b, point)),
        only_reference=sum(1 for a, b in pairs if b is None),
        only_candidate=sum(1 for a, b in pairs if a is None),
        same_exit=sum(
            1 for a, b in matched if a.exit_kind == b.exit_kind and a.exit_time == b.exit_time
        ),
        same_exit_kind=sum(1 for a, b in matched if a.exit_kind == b.exit_kind),
        exit_within_minute=sum(
            1
            for a, b in matched
            if a.exit_kind == b.exit_kind and abs(a.exit_time - b.exit_time) <= ENTRY_TOLERANCE
        ),
        entry_seconds=_distribution(
            [(b.entry_time - a.entry_time).total_seconds() for a, b in matched]
        ),
        entry_points=_distribution(points(matched, "entry_price")),
        entry_points_buys=_distribution(
            points([(a, b) for a, b in matched if a.direction == "buy"], "entry_price")
        ),
        entry_points_sells=_distribution(
            points([(a, b) for a, b in matched if a.direction == "sell"], "entry_price")
        ),
        exit_points=_distribution(points(matched, "exit_price")),
        exit_slippage_reference=_distribution([s for s in slips_ref if s is not None]),
        exit_slippage_candidate=_distribution([s for s in slips_cand if s is not None]),
        result_difference=_distribution([b.result - a.result for a, b in matched]),
        net_profit=(float(ref_metrics["net_profit"]), float(cand_metrics["net_profit"])),
        max_drawdown=(
            float(ref_metrics["balance_dd_maximal"]),
            float(cand_metrics["balance_dd_maximal"]),
        ),
        swap=(
            round(sum(d.swap for d in reference.deals), 2),
            round(sum(d.swap for d in candidate.deals), 2),
        ),
        net_difference=attribute(pairs),
        first_difference=differing[0] if differing else None,
        examples=differing[:5],
    )

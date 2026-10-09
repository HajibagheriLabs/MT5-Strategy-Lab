"""Compare StrategyLab's metrics with the figures MetaTrader printed in the same reports.

    python scripts/metric_crosscheck.py [report.htm ...]

Without arguments every report in tests/fixtures/reports/ is used. Writes
reports/metric_crosscheck.md and exits with status 1 if any figure disagrees for a reason not
explained below.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from _venv import REPO_ROOT, ensure_venv

ensure_venv()

from strategylab.metrics import (  # noqa: E402
    DEFINITIONS,
    METATRADER_EQUIVALENTS,
    compute_metrics,
)
from strategylab.report import ParsedReport, parse_number, parse_report  # noqa: E402

FIXTURES = REPO_ROOT / "tests" / "fixtures" / "reports"
OUTPUT = REPO_ROOT / "reports" / "metric_crosscheck.md"

# Disagreements that come from a different definition, with the reason. Each was investigated
# before being listed here; a formula is never changed just to make a figure match.
EXPLAINED = {
    "recovery_factor": (
        "MetaTrader divides net profit by the maximal *equity* drawdown; a deals table has no "
        "equity, so StrategyLab divides by the maximal *balance* drawdown. The check column "
        "recomputes MetaTrader's figure from its own equity drawdown to show that this is the "
        "whole difference."
    ),
    "sharpe_ratio": (
        "MetaTrader's Sharpe ratio includes equity movements and is scaled to one year (release "
        "notes for build 3210); how equity is sampled is not documented. StrategyLab has only "
        "the balance, so it uses daily balance returns on weekdays, annualised with sqrt(252). "
        "The two measure different things and are not expected to agree."
    ),
}
UNDEFINED_NOTE = (
    "MetaTrader prints 0 for a ratio or average that has no trades to work from; StrategyLab "
    "leaves it empty rather than report a number that was never computed."
)
NOT_FROM_DEALS = {
    "Equity Drawdown Absolute": "needs the equity curve",
    "Equity Drawdown Maximal": "needs the equity curve",
    "Equity Drawdown Relative": "needs the equity curve",
    "Margin Level": "needs margin over time",
    "Z-Score": "not part of StrategyLab's metric set",
    "LR Correlation": "not part of StrategyLab's metric set",
    "LR Standard Error": "not part of StrategyLab's metric set",
    "Correlation (Profits,MFE)": "needs each position's best excursion (MFE)",
    "Correlation (Profits,MAE)": "needs each position's worst excursion (MAE)",
    "Correlation (MFE,MAE)": "needs MFE and MAE",
    "Minimal position holding time": "needs position identifiers the report does not list",
    "Maximal position holding time": "needs position identifiers the report does not list",
    "Average position holding time": "needs position identifiers the report does not list",
    "History Quality": "a property of the tester's data, not of the trades",
    "Bars": "a property of the tester's data, not of the trades",
    "Ticks": "a property of the tester's data, not of the trades",
    "Symbols": "a property of the tester's data, not of the trades",
    "OnTester result": "returned by the EA itself",
}


@dataclass
class Row:
    metric: str
    label: str
    printed: str
    ours: float | int | None
    status: str
    detail: str = ""


def figure(text: str, part: int) -> tuple[float | None, int]:
    """One figure of a report cell, and the number of decimals it was printed with."""
    head, _, tail = text.partition("(")
    piece = (head if part == 0 else tail).replace(")", "").replace("%", "").strip()
    value = parse_number(piece)
    decimals = len(piece.rpartition(".")[2]) if "." in piece else 0
    return value, decimals


def compare(report: ParsedReport) -> list[Row]:
    ours = compute_metrics(report.deals)
    rows = []
    for metric, (label, part) in METATRADER_EQUIVALENTS.items():
        printed = report.summary.get(label)
        mine = ours[metric]
        if printed is None:
            rows.append(Row(metric, label, "-", mine, "not in report"))
            continue
        theirs, decimals = figure(printed, part)
        if mine is None:
            agree = theirs == 0
            status = "undefined" if agree else "DIFFERS"
            rows.append(Row(metric, label, printed, mine, status))
            continue
        if theirs is not None and abs(round(float(mine), decimals) - theirs) < 1e-9:
            rows.append(Row(metric, label, printed, mine, "agrees"))
            continue
        status = "definition" if metric in EXPLAINED else "DIFFERS"
        detail = ""
        if metric == "recovery_factor":
            equity_dd, _ = figure(report.summary.get("Equity Drawdown Maximal", ""), 0)
            if equity_dd:
                detail = f"net profit / equity drawdown = {ours['net_profit'] / equity_dd:.2f}"
        rows.append(Row(metric, label, printed, mine, status, detail))
    return rows


def show(value: float | int | None) -> str:
    if value is None:
        return "empty"
    if isinstance(value, int):
        return str(value)
    return f"{value:.4f}".rstrip("0").rstrip(".") if abs(value) < 100 else f"{value:.2f}"


def describe(report: ParsedReport, path: Path) -> str:
    s = report.settings
    return (
        f"`{path.name}`: {s.expert} on {s.symbol} {s.timeframe}, {s.date_from:%Y.%m.%d} to "
        f"{s.date_to:%Y.%m.%d}, {report.trade_count} trades, {s.server} build {s.build}"
    )


def render(results: list[tuple[Path, ParsedReport, list[Row]]]) -> str:
    out = [
        "# Metric cross-check",
        "",
        "StrategyLab computes its metrics from the deals table alone, so that a Strategy Tester "
        "run and a simulated run are measured the same way. This page compares those figures "
        "with the ones MetaTrader printed in the same reports. It is generated by "
        "`python scripts/metric_crosscheck.py`; the definitions are in "
        "`backend/strategylab/metrics.py`.",
        "",
        "A figure **agrees** when StrategyLab's value, rounded to the decimals MetaTrader "
        "printed, equals MetaTrader's. Every disagreement is explained below the tables; none "
        "is left unexplained.",
        "",
        "Reports compared (real Strategy Tester output on a demo account, 1 minute OHLC):",
        "",
    ]
    out += [f"- {describe(report, path)}" for path, report, _ in results]
    for path, _, rows in results:
        out += ["", f"## {path.stem}", ""]
        out += [
            "| Metric | MetaTrader label | MetaTrader | StrategyLab | Result |",
            "|---|---|---|---|---|",
        ]
        for row in rows:
            result = {
                "agrees": "agrees",
                "undefined": "differs: undefined, see below",
                "definition": "differs: definition, see below",
                "not in report": "not in report",
            }.get(row.status, "**differs, unexplained**")
            if row.detail:
                result += f" ({row.detail})"
            out.append(
                f"| `{row.metric}` | {row.label} | {row.printed} | {show(row.ours)} | {result} |"
            )
    out += ["", "## Disagreements explained", ""]
    for metric, reason in EXPLAINED.items():
        out += [f"**`{metric}`.** {reason}", ""]
    out += [f"**Empty where MetaTrader prints 0.** {UNDEFINED_NOTE}", ""]
    out += [
        "## Agreements worth knowing about",
        "",
        "- A trade's result is the closing deal's profit + swap + commission. Taking profit "
        "alone does not reproduce MetaTrader's gross profit, gross loss or averages; adding swap "
        "does, to the cent.",
        "- MetaTrader prints the average length of winning and losing runs as whole numbers "
        "(1.23 shows as 1, 3.20 as 3). StrategyLab keeps the fraction; the two agree at "
        "MetaTrader's precision.",
        "- Maximal and relative balance drawdown use a running high that starts at the "
        "deposit, which reproduces MetaTrader's figures exactly.",
        "- When several winning (or losing) runs share the longest length, MetaTrader reports "
        "the one with the largest total profit (or loss), not the first or the last. This was "
        "established on nine tester reports before being adopted: six cases had tied runs, and in "
        "`macd_gbpusd_m5_2025` the last tied winning run (114.12) and the most profitable one "
        "(114.60) differ, with MetaTrader showing 114.60.",
        "",
        "## Not checked here",
        "",
        "- **Commission.** The demo account charges none, so every report has zero commission. "
        "StrategyLab counts commission on entry deals in net profit but, like the closing-deal "
        "rule above, attributes only the closing deal's commission to a trade. Whether "
        "MetaTrader's gross figures do the same is not verified.",
        "- **MetaTrader figures StrategyLab does not compute:**",
        "",
    ]
    out += [f"  - {label}: {why}" for label, why in NOT_FROM_DEALS.items()]
    out += ["", "## Definitions", ""]
    out += [f"- `{metric}`: {text}" for metric, text in DEFINITIONS.items()]
    return "\n".join(out) + "\n"


def main(argv: list[str]) -> int:
    paths = [Path(arg) for arg in argv] or sorted(FIXTURES.glob("*.htm"))
    results = []
    for path in paths:
        report = parse_report(path)
        results.append((path, report, compare(report)))
    OUTPUT.parent.mkdir(exist_ok=True)
    OUTPUT.write_text(render(results), encoding="utf-8")
    unexplained = [
        (path.name, row.metric)
        for path, _, rows in results
        for row in rows
        if row.status == "DIFFERS"
    ]
    for path, _, rows in results:
        counts: dict[str, int] = {}
        for row in rows:
            counts[row.status] = counts.get(row.status, 0) + 1
        print(path.name, counts)
    print(f"wrote {OUTPUT.relative_to(REPO_ROOT)}")
    if unexplained:
        print("unexplained differences:", unexplained)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

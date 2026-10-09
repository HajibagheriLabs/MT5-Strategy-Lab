"""Measure how far the Python simulator lands from the Strategy Tester.

    python scripts/parity.py                  run every backtest, then write reports/parity.md
    python scripts/parity.py --reuse          rewrite the report from the last runs
    python scripts/parity.py --rerun-python   keep the tester runs, run the simulator again

The same strategy, written twice (samples/parity/), runs on each symbol and timeframe in the
tester with 1 minute OHLC and with real ticks, and in the simulator on 1-minute bars. A shorter
window also runs the simulator on recorded ticks. Trades are then compared one by one.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from datetime import date
from pathlib import Path

from _venv import REPO_ROOT, ensure_venv

ensure_venv()

from strategylab.backtest import run_mql5_backtest, run_python_strategy  # noqa: E402
from strategylab.config import Settings, load_settings  # noqa: E402
from strategylab.parity import Comparison, Trade, compare  # noqa: E402
from strategylab.result import BacktestResult, TickModel  # noqa: E402

EA = REPO_ROOT / "samples" / "parity" / "ParityMACross.mq5"
SCRIPT = REPO_ROOT / "samples" / "parity" / "ma_cross_parity.py"
OUTPUT = REPO_ROOT / "reports" / "parity.md"
DATA = REPO_ROOT / "reports" / "parity.json"
POINTS = {"EURUSD@": 0.00001, "USDJPY@": 0.001, "GBPUSD@": 0.00001}


def runs_file(settings: Settings) -> Path:
    return settings.workspace_dir / "parity" / "runs.json"


def run_matrix(
    args: argparse.Namespace, settings: Settings, keep: dict[str, str] | None = None
) -> dict[str, str]:
    """Run every backtest; return result.json paths keyed by run name.

    Runs found in `keep` (tester runs, when only the simulator changed) are not run again.
    """
    keep = keep or {}
    paths: dict[str, str] = {}
    window = {"date_from": args.date_from, "date_to": args.date_to}
    for symbol in args.symbols:
        for timeframe in args.timeframes:
            key = f"{symbol}|{timeframe}"
            for model in (TickModel.OHLC_M1, TickModel.REAL_TICKS):
                if f"{key}|tester_{model}" in keep:
                    paths[f"{key}|tester_{model}"] = keep[f"{key}|tester_{model}"]
                    continue
                started = time.monotonic()
                run = run_mql5_backtest(
                    EA, settings, symbol=symbol, timeframe=timeframe, model=model,
                    timeout_s=4 * 3600, **window,
                )  # fmt: skip
                if run.result_path is None:
                    sys.exit(f"tester {key} {model}: {run.tester.message}")
                paths[f"{key}|tester_{model}"] = str(run.result_path)
                print(
                    f"tester {key} {model}: {run.tester.message} "
                    f"({time.monotonic() - started:.0f} s)"
                )
            started = time.monotonic()
            sim = run_python_strategy(
                SCRIPT, settings, symbol=symbol, timeframe=timeframe,
                args=("--symbol", symbol, "--timeframe", timeframe), **window,
            )  # fmt: skip
            if sim.result_path is None:
                sys.exit(f"python {key}: {sim.message}")
            paths[f"{key}|python_m1"] = str(sim.result_path)
            print(f"python {key}: {sim.message} ({time.monotonic() - started:.0f} s)")
    symbol, timeframe = args.symbols[0], args.timeframes[0]
    key = f"{symbol}|{timeframe}|ticks_window"
    window = {"date_from": args.ticks_from, "date_to": args.ticks_to}
    tester_key = f"{key}|tester_{TickModel.REAL_TICKS}"
    if tester_key in keep:
        paths[tester_key] = keep[tester_key]
    else:
        run = run_mql5_backtest(
            EA, settings, symbol=symbol, timeframe=timeframe, model=TickModel.REAL_TICKS,
            timeout_s=4 * 3600, **window,
        )  # fmt: skip
        paths[tester_key] = str(run.result_path)
    sim = run_python_strategy(
        SCRIPT, settings, symbol=symbol, timeframe=timeframe, granularity="ticks",
        args=("--symbol", symbol, "--timeframe", timeframe), timeout_s=4 * 3600, **window,
    )  # fmt: skip
    paths[f"{key}|python_ticks"] = str(sim.result_path)
    print(f"ticks window: python {sim.message}")
    return paths


def load(path: str) -> BacktestResult:
    return BacktestResult.model_validate_json(Path(path).read_text(encoding="utf-8"))


COMPARISONS = (
    (
        "python_m1",
        f"tester_{TickModel.OHLC_M1}",
        "simulator (1-minute bars) vs tester, 1 minute OHLC",
    ),
    (
        "python_m1",
        f"tester_{TickModel.REAL_TICKS}",
        "simulator (1-minute bars) vs tester, real ticks",
    ),
    (
        "python_ticks",
        f"tester_{TickModel.REAL_TICKS}",
        "simulator (real ticks) vs tester, real ticks",
    ),
)


def comparisons(paths: dict[str, str]) -> list[tuple[str, str, str, Comparison]]:
    found = []
    keys = sorted({k.rsplit("|", 1)[0] for k in paths})
    for key in keys:
        symbol = key.split("|")[0]
        for candidate, reference, label in COMPARISONS:
            a, b = paths.get(f"{key}|{reference}"), paths.get(f"{key}|{candidate}")
            if a and b:
                found.append((key, label, symbol, compare(load(a), load(b), POINTS[symbol])))
    return found


def pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def money(value: float) -> str:
    return f"{value:,.2f}".replace(",", " ")


def show_trade(trade: Trade | None) -> str:
    if trade is None:
        return "no trade"
    return (
        f"{trade.direction} {trade.entry_time:%m-%d %H:%M:%S} @ {trade.entry_price}, out "
        f"{trade.exit_time:%m-%d %H:%M:%S} @ {trade.exit_price} ({trade.exit_kind}), {trade.result}"
    )


def render(found: list[tuple[str, str, str, Comparison]], args: argparse.Namespace) -> str:
    out = [
        "# Parity study: Python simulator against the Strategy Tester",
        "",
        "One deliberately simple strategy, written twice with the same logic step for step "
        "(`samples/parity/ParityMACross.mq5` and `samples/parity/ma_cross_parity.py`): a "
        "10/30 moving-average cross on closed bars, 0.10 lots, stop loss 200 points, take "
        "profit 400 points, one position at a time, so every exit is a stop loss, a take "
        "profit or the end of the run. Both run on the same symbols, timeframes and dates on "
        "a demo account. Generated by `python scripts/parity.py`.",
        "",
        f"Main window: {args.date_from:%Y-%m-%d} to {args.date_to:%Y-%m-%d} (end exclusive). "
        f"The simulator on recorded ticks runs on a shorter window, {args.ticks_from:%Y-%m-%d} "
        f"to {args.ticks_to:%Y-%m-%d}, because a tick export is large.",
        "",
        "Trades are paired by direction and entry time (within a minute). **Identical** means "
        "same entry and exit second, same prices to the point, and the same result to the "
        "cent. Price differences are in points, simulator minus tester.",
        "",
        "## Summary",
        "",
        "| Run | Comparison | Trades tester / sim | Paired | Identical "
        "| Net profit tester / sim | Max drawdown tester / sim |",
        "|---|---|---|---|---|---|---|",
    ]
    for key, label, _, c in found:
        out.append(
            f"| {key.replace('|', ' ').replace(' ticks_window', ' (tick window)')} | {label} "
            f"| {c.reference_trades} / {c.candidate_trades} | {pct(c.match_rate)} "
            f"| {pct(c.identical_rate)} | {money(c.net_profit[0])} / {money(c.net_profit[1])} "
            f"| {money(c.max_drawdown[0])} / {money(c.max_drawdown[1])} |"
        )
    out += ["", "## Details", ""]
    for key, label, _, c in found:
        out += [
            f"### {key.replace('|', ' ')}: {label}",
            "",
            "| | median | 95th pct (abs) | max (abs) | exactly equal |",
            "|---|---|---|---|---|",
        ]
        for name, dist in (
            ("Entry time, seconds", c.entry_seconds),
            ("Entry price, points (buys)", c.entry_points_buys),
            ("Entry price, points (sells)", c.entry_points_sells),
            ("Exit price, points", c.exit_points),
            ("Exit beyond SL/TP level, tester, points", c.exit_slippage_reference),
            ("Exit beyond SL/TP level, simulator, points", c.exit_slippage_candidate),
            ("Trade result, money", c.result_difference),
        ):
            if not dist.get("count"):
                continue
            out.append(
                f"| {name} | {dist['median']:.2f} | {dist['p95_abs']:.2f} | {dist['max_abs']:.2f} "
                f"| {pct(dist['zero_share'])} |"
            )
        out += [
            "",
            f"Of {c.matched} paired trades, {c.same_exit_kind} closed the same way (stop "
            f"loss, take profit or end of run), {c.exit_within_minute} within the same minute "
            f"and {c.same_exit} in the same second. "
            f"Unpaired: {c.only_reference} tester only, {c.only_candidate} simulator only. "
            f"Swap total: tester {money(c.swap[0])}, simulator {money(c.swap[1])}.",
            "",
        ]
        if c.first_difference:
            a, b = c.first_difference
            out += [
                "First difference:",
                "",
                f"- tester: {show_trade(a)}",
                f"- simulator: {show_trade(b)}",
                "",
            ]
    out += render_findings(found)
    return "\n".join(out) + "\n"


def render_findings(found: list[tuple[str, str, str, Comparison]]) -> list[str]:
    path = REPO_ROOT / "reports" / "parity_findings.md"
    if path.is_file():
        return ["", path.read_text(encoding="utf-8").rstrip()]
    return []


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reuse", action="store_true", help="use the last runs")
    parser.add_argument(
        "--rerun-python", action="store_true", help="keep the tester runs, rerun the simulator"
    )
    parser.add_argument("--symbols", nargs="+", default=["EURUSD@", "USDJPY@"])
    parser.add_argument("--timeframes", nargs="+", default=["H1", "M15"])
    parser.add_argument(
        "--from", dest="date_from", type=date.fromisoformat, default=date(2025, 1, 1)
    )
    parser.add_argument("--to", dest="date_to", type=date.fromisoformat, default=date(2025, 4, 1))
    parser.add_argument("--ticks-from", type=date.fromisoformat, default=date(2025, 3, 3))
    parser.add_argument("--ticks-to", type=date.fromisoformat, default=date(2025, 3, 29))
    args = parser.parse_args(argv)
    settings = load_settings()
    store = runs_file(settings)
    if args.reuse:
        paths = json.loads(store.read_text(encoding="utf-8"))
    else:
        keep = {}
        if args.rerun_python:
            previous = json.loads(store.read_text(encoding="utf-8"))
            keep = {k: v for k, v in previous.items() if "|tester_" in k}
        paths = run_matrix(args, settings, keep)
        store.parent.mkdir(parents=True, exist_ok=True)
        store.write_text(json.dumps(paths, indent=1), encoding="utf-8")
    found = comparisons(paths)
    OUTPUT.write_text(render(found, args), encoding="utf-8")
    DATA.write_text(
        json.dumps(
            [
                {
                    "run": key,
                    "comparison": label,
                    **{
                        k: v
                        for k, v in asdict(c).items()
                        if k not in ("first_difference", "examples")
                    },
                }
                for key, label, _, c in found
            ],
            indent=1,
        ),
        encoding="utf-8",
    )
    print(f"wrote {OUTPUT.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

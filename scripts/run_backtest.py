"""Compile an MQL5 strategy, run it in the Strategy Tester and print a summary.

    python scripts/run_backtest.py --symbol EURUSD --timeframe H1 --from 2025-01-01 --to 2026-01-01
    python scripts/run_backtest.py path/to/ea.mq5 --symbol EURUSD --set MovingPeriod=24

Without a strategy path the shipped "Moving Average" example is used. The end date is exclusive,
exactly as in the Strategy Tester. Everything the run produced (configuration, report, journals and
the normalised result) is kept in workspace/runs/<run id>/.

Exit status: 0 the test ran (with or without trades), 1 compile errors, 2 MetaTrader could not be
found or used, 3 no report, 4 timeout, 5 the terminal was already running.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from _venv import ensure_venv

ensure_venv()

try:
    from strategylab.backtest import BacktestRun, CompileFailedError, run_mql5_backtest
    from strategylab.compiler import CompileError
    from strategylab.config import ConfigError, Settings, load_settings
    from strategylab.result import TickModel
    from strategylab.server_clock import describe
    from strategylab.tester import DEFAULT_TIMEOUT_S, MODEL_CODES, TIMEFRAMES, Outcome, SpecError
except ImportError:
    sys.exit("StrategyLab is not installed. Run `python tasks.py setup` first.")

MODEL_NAMES = {
    TickModel.EVERY_TICK: "Every tick",
    TickModel.OHLC_M1: "1 minute OHLC",
    TickModel.OPEN_PRICES: "Open prices only",
    TickModel.REAL_TICKS: "Every tick based on real ticks",
}
EXIT_CODES = {
    Outcome.SUCCESS: 0,
    Outcome.ZERO_TRADES: 0,
    Outcome.NO_REPORT: 3,
    Outcome.TIMEOUT: 4,
    Outcome.TERMINAL_RUNNING: 5,
}
SUMMARY_ROWS = (
    "Total Net Profit",
    "Gross Profit",
    "Gross Loss",
    "Profit Factor",
    "Expected Payoff",
    "Total Trades",
    "Total Deals",
    "Balance Drawdown Maximal",
    "Balance Drawdown Relative",
    "Equity Drawdown Maximal",
    "Equity Drawdown Relative",
    "Sharpe Ratio",
    "Recovery Factor",
    "History Quality",
)


def parse_parameter(text: str) -> tuple[str, str]:
    name, sep, value = text.partition("=")
    if not sep or not name.strip():
        raise argparse.ArgumentTypeError(f"expected Name=Value, got '{text}'")
    return name.strip(), value.strip()


def print_summary(run: BacktestRun, settings: Settings, args: argparse.Namespace) -> None:
    tester = run.tester
    terminal = settings.terminal
    expert = run.compile.strategy.expert_path(terminal)
    print(f"Strategy   {run.compile.strategy.entry}  ({expert})")
    print(
        f"Test       {args.symbol} {args.timeframe}, {args.date_from:%Y.%m.%d} to "
        f"{args.date_to:%Y.%m.%d} (end exclusive), {MODEL_NAMES[args.model]}"
    )
    print(f"Account    {args.deposit:,.2f} {args.currency.upper()}, 1:{args.leverage}")
    print(f"Outcome    {tester.outcome}: {tester.message} ({tester.elapsed_s:.1f} s)")
    for note in run.notes:
        print(f"Note       {note}")
    result = run.result
    if result is not None:
        reported = result.reported
        net_from_deals = sum(
            d.profit + d.commission + d.swap for d in result.deals if d.type != "balance"
        )
        print()
        print(f"{'':28}{'MetaTrader':>18}{'from deals':>14}")
        for label in SUMMARY_ROWS:
            ours = ""
            if label == "Total Net Profit":
                ours = f"{net_from_deals:,.2f}".replace(",", " ")
            elif label == "Total Trades":
                ours = str(result.trade_count)
            print(f"{label:28}{reported.get(label, '-'):>18}{ours:>14}")
        meta = result.meta
        print()
        print(
            f"Server     {meta.server} (build {meta.terminal_build}), "
            f"clock {describe(meta.server_utc_offsets_h)}"
        )
        for note in result.meta.notes:
            print(f"Note       {note}")
        if result.meta.parameters:
            inputs = ", ".join(f"{k}={v}" for k, v in result.meta.parameters.items())
            print(f"Inputs     {inputs}")
    print(f"Run folder {tester.run_dir}")
    if tester.outcome in (Outcome.SUCCESS, Outcome.ZERO_TRADES):
        print()
        print("To repeat by hand in the Strategy Tester: Expert", f'"{expert[:-4]}",')
        print(
            f"  Symbol {args.symbol}, Period {args.timeframe}, Date Custom period "
            f"{args.date_from:%Y.%m.%d} - {args.date_to:%Y.%m.%d}, Modelling "
            f'"{MODEL_NAMES[args.model]}", Deposit {args.deposit:g} {args.currency.upper()}, '
            f"Leverage 1:{args.leverage}, Optimization disabled, inputs as listed above."
        )


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("strategy", nargs="?", type=Path, help=".mq5, .ex5 or .zip")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--timeframe", default="H1", choices=TIMEFRAMES)
    parser.add_argument("--from", dest="date_from", type=date.fromisoformat, required=True)
    parser.add_argument("--to", dest="date_to", type=date.fromisoformat, required=True)
    parser.add_argument(
        "--model", type=TickModel, default=TickModel.OHLC_M1, choices=list(MODEL_CODES)
    )
    parser.add_argument("--deposit", type=float, default=10_000.0)
    parser.add_argument("--currency", default="USD")
    parser.add_argument("--leverage", type=int, default=100)
    parser.add_argument("--set", dest="parameters", type=parse_parameter, action="append")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S)
    args = parser.parse_args(argv)

    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"MetaTrader 5 could not be set up:\n  {exc}")
        return 2
    strategy = args.strategy or (
        settings.terminal.experts_dir / "Examples" / "Moving Average" / "Moving Average.mq5"
    )
    try:
        run = run_mql5_backtest(
            strategy,
            settings,
            symbol=args.symbol,
            timeframe=args.timeframe,
            date_from=args.date_from,
            date_to=args.date_to,
            model=args.model,
            deposit=args.deposit,
            currency=args.currency,
            leverage=args.leverage,
            parameters=dict(args.parameters or []),
            timeout_s=args.timeout,
        )
    except CompileFailedError as exc:
        print(exc)
        for diagnostic in exc.result.diagnostics:
            print(f"  {diagnostic}")
        return 1
    except (CompileError, SpecError) as exc:
        print(exc)
        return 2
    print_summary(run, settings, args)
    return EXIT_CODES[run.tester.outcome]


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

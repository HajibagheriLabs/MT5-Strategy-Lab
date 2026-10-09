"""Run a Python strategy written against the MetaTrader5 package in the simulator.

    python scripts/run_python_backtest.py samples/python/ma_cross.py --symbol EURUSD \\
        --timeframe H1 --from 2025-01-01 --to 2026-01-01

The script runs unmodified: it imports MetaTrader5 and gets the simulator, and its time.sleep()
moves simulated time. History is exported from the configured terminal first and cached. The end
date is exclusive. Everything is kept in workspace/runs/<run id>/, including the strategy's own
output in strategy.log.

Exit status: 0 the strategy ran (with or without trades), 1 it raised an error, 2 MetaTrader
could not be used, 4 timeout.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from _venv import ensure_venv

ensure_venv()

try:
    from strategylab.backtest import run_python_strategy
    from strategylab.config import ConfigError, load_settings
    from strategylab.mt5_data import MT5DataError
    from strategylab.server_clock import describe
    from strategylab.sim.runner import DEFAULT_TIMEOUT_S, SimOutcome
except ImportError:
    sys.exit("StrategyLab is not installed. Run `python tasks.py setup` first.")

EXIT_CODES = {
    SimOutcome.SUCCESS: 0,
    SimOutcome.ZERO_TRADES: 0,
    SimOutcome.ERROR: 1,
    SimOutcome.TIMEOUT: 4,
}
ROWS = (
    ("Net profit", "net_profit"),
    ("Trades", "trades"),
    ("Win rate %", "win_rate_pct"),
    ("Profit factor", "profit_factor"),
    ("Max balance drawdown", "balance_dd_maximal"),
    ("Max balance drawdown %", "balance_dd_relative_pct"),
    ("Sharpe ratio", "sharpe_ratio"),
)


def show(value: float | int | None) -> str:
    if value is None:
        return "-"
    return str(value) if isinstance(value, int) else f"{value:,.2f}".replace(",", " ")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("script", type=Path)
    parser.add_argument("--symbol", required=True, help="as named on the server, e.g. EURUSD@")
    parser.add_argument("--timeframe", default="H1", help="the timeframe the strategy trades on")
    parser.add_argument("--from", dest="date_from", type=date.fromisoformat, required=True)
    parser.add_argument("--to", dest="date_to", type=date.fromisoformat, required=True)
    parser.add_argument("--deposit", type=float, default=10_000.0)
    parser.add_argument("--currency", default="USD")
    parser.add_argument("--leverage", type=int, default=100)
    parser.add_argument("--commission", type=float, default=0.0, help="per lot, per deal")
    parser.add_argument("--ticks", action="store_true", help="use recorded ticks, not M1 bars")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S)
    args = parser.parse_args(argv)
    if not args.script.is_file():
        print(f"{args.script} does not exist.")
        return 2
    try:
        settings = load_settings()
        run = run_python_strategy(
            args.script,
            settings,
            symbol=args.symbol,
            timeframe=args.timeframe,
            date_from=args.date_from,
            date_to=args.date_to,
            deposit=args.deposit,
            currency=args.currency,
            leverage=args.leverage,
            commission_per_lot=args.commission,
            granularity="ticks" if args.ticks else "m1_ohlc",
            timeout_s=args.timeout,
        )
    except (ConfigError, MT5DataError) as exc:
        print(exc)
        return 2

    print(f"Strategy   {args.script.name}")
    print(
        f"Test       {args.symbol}, {args.date_from:%Y.%m.%d} to {args.date_to:%Y.%m.%d} "
        "(end exclusive)"
    )
    print(f"Outcome    {run.outcome}: {run.message} ({run.elapsed_s:.1f} s)")
    if run.stats:
        print(f"Simulated  {run.stats['sleeps']} sleeps, ended because {run.stats['status']}")
    if run.result is not None:
        meta = run.result.meta
        print(f"Fidelity   {meta.fidelity}")
        print(f"Clock      {describe(meta.server_utc_offsets_h)}")
        for note in meta.notes:
            print(f"Note       {note}")
        print()
        for label, key in ROWS:
            print(f"{label:24}{show(run.result.metrics.get(key)):>14}")
    print(f"Run folder {run.run_dir}")
    return EXIT_CODES[run.outcome]


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

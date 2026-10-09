"""Read-only access to a MetaTrader 5 terminal through the MetaTrader5 package.

This is the only module in StrategyLab that imports the real package, and it only calls functions
that read: initialize and shutdown, terminal, account and symbol information, and price history.
Nothing here places, modifies or closes an order; a test scans this file to keep it that way.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import MetaTrader5 as mt5

from strategylab.config import TerminalInstall, path_key, terminals_using
from strategylab.server_clock import ServerClock, load_cached, save_cached
from strategylab.winproc import stop_process

INIT_TIMEOUT_MS = 60_000
# History is downloaded in the background after a request; ask again until the answer settles.
HISTORY_POLLS = 10
HISTORY_POLL_SECONDS = 1.0
CLOCK_LOOKBACK = timedelta(days=21)
REFERENCE_SYMBOLS = ("EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCHF")


class MT5DataError(Exception):
    pass


class TerminalSession:
    """An attached terminal. Use `open_session`, which also closes a terminal it had to start."""

    def __init__(self, install: TerminalInstall) -> None:
        self.install = install
        account = mt5.account_info()
        terminal = mt5.terminal_info()
        if account is None or terminal is None:
            raise MT5DataError(f"The terminal answered without account data: {mt5.last_error()}")
        self.server: str = account.server
        self.demo: bool = account.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO
        self.currency: str = account.currency
        self.build: int = terminal.build
        self.max_bars: int = terminal.maxbars

    def symbol_names(self) -> list[str]:
        symbols = mt5.symbols_get()
        return [symbol.name for symbol in symbols or ()]

    def reference_fx_symbol(self) -> str:
        """An FX major on this server, allowing for broker suffixes such as EURUSD@ or EURUSD.m."""
        names = self.symbol_names()
        for base in REFERENCE_SYMBOLS:
            matches = sorted((n for n in names if n.upper().startswith(base)), key=len)
            if matches:
                return matches[0]
        raise MT5DataError(
            f"No FX major ({', '.join(REFERENCE_SYMBOLS)}) is offered on {self.server}, so its "
            "server clock cannot be measured."
        )

    def bar_times(
        self, symbol: str, timeframe: int, start: datetime, end: datetime
    ) -> list[datetime]:
        """Open times of bars in [start, end), in server time."""
        if not mt5.symbol_select(symbol, True):
            raise MT5DataError(f"Cannot select {symbol}: {mt5.last_error()}")
        previous = -1
        rates = None
        for _ in range(HISTORY_POLLS):
            rates = mt5.copy_rates_range(symbol, timeframe, start, end)
            count = 0 if rates is None else len(rates)
            if count and count == previous:
                break
            previous = count
            time.sleep(HISTORY_POLL_SECONDS)
        if rates is None or len(rates) == 0:
            raise MT5DataError(
                f"No history for {symbol} between {start} and {end}: {mt5.last_error()}"
            )
        return [datetime.fromtimestamp(int(t), UTC).replace(tzinfo=None) for t in rates["time"]]

    def measure_clock(self, start: date) -> ServerClock:
        symbol = self.reference_fx_symbol()
        begin = datetime(start.year, start.month, start.day) - CLOCK_LOOKBACK
        # Bar times from the package are server time expressed as seconds since 1970, so the
        # request range is given the same way: naive server time marked as UTC.
        times = self.bar_times(
            symbol,
            mt5.TIMEFRAME_H1,
            begin.replace(tzinfo=UTC),
            (datetime.now(UTC) + timedelta(days=2)),
        )
        return ServerClock.from_bars(self.server, symbol, times)


@contextmanager
def open_session(install: TerminalInstall) -> Iterator[TerminalSession]:
    was_running = bool(terminals_using(install))
    if not mt5.initialize(
        path=str(install.terminal_exe), portable=install.portable, timeout=INIT_TIMEOUT_MS
    ):
        code, message = mt5.last_error()
        raise MT5DataError(
            f"Could not attach to {install.terminal_exe} ({code}: {message}). Check that it starts "
            "by hand and is logged in to an account."
        )
    try:
        data_path = mt5.terminal_info().data_path
        if path_key(data_path) != path_key(install.data_dir):
            raise MT5DataError(
                f"Attached to a terminal using {data_path}, not {install.data_dir}. Close other "
                "terminals started from the same folder and try again."
            )
        yield TerminalSession(install)
    finally:
        mt5.shutdown()
        if not was_running:
            # The package starts the terminal when needed but never stops it; the tester needs
            # this data folder to itself afterwards.
            for running in terminals_using(install):
                stop_process(running.pid)


def ensure_server_clock(
    install: TerminalInstall,
    cache_dir: Path,
    server_hint: str | None,
    start: date,
    end: date,
) -> ServerClock:
    """The server clock covering [start, end), from cache when possible, else measured now."""
    if server_hint:
        cached = load_cached(cache_dir, server_hint)
        if cached is not None and cached.covers(start, end):
            return cached
    with open_session(install) as session:
        cached = load_cached(cache_dir, session.server)
        if cached is not None and cached.covers(start, end):
            return cached
        earliest = min(start, cached.first_week.date()) if cached else start
        clock = session.measure_clock(earliest)
    save_cached(cache_dir, clock)
    return clock

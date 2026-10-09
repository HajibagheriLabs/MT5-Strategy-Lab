"""Read-only access to a MetaTrader 5 terminal through the MetaTrader5 package.

This is the only module in StrategyLab that imports the real package, and it only calls functions
that read: initialize and shutdown, terminal, account and symbol information, symbol selection,
and price history. Nothing here places, modifies or closes an order; a test scans this file to
keep it that way, and the terminal is started with live trading switched off besides.
"""

from __future__ import annotations

import codecs
import json
import subprocess
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import MetaTrader5 as mt5
import pandas as pd

from strategylab.config import TerminalInstall, path_key, terminals_using
from strategylab.server_clock import ServerClock, load_cached, save_cached
from strategylab.winproc import stop_process

INIT_TIMEOUT_MS = 60_000
# History is downloaded in the background after a request; ask again until the answer settles.
HISTORY_POLLS = 15
HISTORY_POLL_SECONDS = 1.0
CLOCK_LOOKBACK = timedelta(days=21)
REFERENCE_SYMBOLS = ("EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCHF")
# The package only returns bars inside "Max bars in chart" (100 000 by default, about 70 days of
# M1). The data session raises it for its own lifetime through the start configuration.
SESSION_MAX_BARS = 10_000_000
TIMEFRAMES = {
    "M1": mt5.TIMEFRAME_M1,
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1,
    "H4": mt5.TIMEFRAME_H4,
    "D1": mt5.TIMEFRAME_D1,
}
RATE_COLUMNS = ["time", "open", "high", "low", "close", "tick_volume", "spread", "real_volume"]
TICK_COLUMNS = ["time", "bid", "ask", "last", "volume", "time_msc", "flags", "volume_real"]


class MT5DataError(Exception):
    pass


def _server_epoch(day: date) -> datetime:
    # The package takes and returns server time expressed as seconds since 1970, so request
    # bounds are given the same way: the server date marked as UTC.
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


class TerminalSession:
    """An attached terminal. Use `open_session`, which also closes a terminal it started."""

    def __init__(self, install: TerminalInstall) -> None:
        self.install = install
        account = mt5.account_info()
        terminal = mt5.terminal_info()
        if account is None or terminal is None:
            raise MT5DataError(f"The terminal answered without account data: {mt5.last_error()}")
        self.server: str = account.server
        self.demo: bool = account.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO
        self.currency: str = account.currency
        self.margin_mode: int = account.margin_mode
        self.build: int = terminal.build
        self.max_bars: int = terminal.maxbars

    def symbol_names(self) -> list[str]:
        return [symbol.name for symbol in mt5.symbols_get() or ()]

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

    def _select(self, symbol: str) -> None:
        if not mt5.symbol_select(symbol, True):
            raise MT5DataError(
                f"{symbol} is not offered on {self.server} ({mt5.last_error()}). Check the name "
                "in the terminal's Market Watch, including any suffix such as @ or .m."
            )

    def _settled(self, fetch: Any) -> Any:
        previous = -1
        result = None
        for _ in range(HISTORY_POLLS):
            result = fetch()
            count = 0 if result is None else len(result)
            if count and count == previous:
                break
            previous = count
            time.sleep(HISTORY_POLL_SECONDS)
        return result

    def rates(self, symbol: str, timeframe: str, start: date, end: date) -> pd.DataFrame:
        """Bars with open time in [start, end) in server time, fetched a month at a time."""
        self._select(symbol)
        code = TIMEFRAMES[timeframe]
        frames = []
        chunk_start = start
        while chunk_start < end:
            chunk_end = min(end, (chunk_start.replace(day=1) + timedelta(days=32)).replace(day=1))
            low, high = _server_epoch(chunk_start), _server_epoch(chunk_end)
            rates = self._settled(
                lambda low=low, high=high: mt5.copy_rates_range(symbol, code, low, high)
            )
            if rates is not None and len(rates):
                frame = pd.DataFrame(rates)[RATE_COLUMNS]
                frames.append(frame[frame["time"] < int(high.timestamp())])
            chunk_start = chunk_end
        if not frames:
            return pd.DataFrame({c: pd.Series(dtype="int64") for c in RATE_COLUMNS})
        return pd.concat(frames, ignore_index=True).drop_duplicates("time").reset_index(drop=True)

    def rates_before(self, symbol: str, timeframe: str, start: date, count: int) -> pd.DataFrame:
        """The `count` bars that open before `start`, for strategies that look back."""
        self._select(symbol)
        anchor = _server_epoch(start) - timedelta(seconds=1)
        code = TIMEFRAMES[timeframe]
        rates = self._settled(lambda: mt5.copy_rates_from(symbol, code, anchor, count))
        if rates is None or not len(rates):
            return pd.DataFrame({c: pd.Series(dtype="int64") for c in RATE_COLUMNS})
        return pd.DataFrame(rates)[RATE_COLUMNS]

    def ticks(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        """Every recorded tick in [start, end), fetched a day at a time."""
        self._select(symbol)
        frames = []
        day = start
        while day < end:
            low, high = _server_epoch(day), _server_epoch(day + timedelta(days=1))
            ticks = self._settled(
                lambda low=low, high=high: mt5.copy_ticks_range(
                    symbol, low, high, mt5.COPY_TICKS_ALL
                )
            )
            if ticks is not None and len(ticks):
                frames.append(pd.DataFrame(ticks)[TICK_COLUMNS])
            day += timedelta(days=1)
        if not frames:
            return pd.DataFrame({c: pd.Series(dtype="int64") for c in TICK_COLUMNS})
        return pd.concat(frames, ignore_index=True)

    def symbol_spec(self, symbol: str) -> dict[str, Any]:
        """Everything the terminal reports about a symbol, plus the account's currency and mode."""
        self._select(symbol)
        info = mt5.symbol_info(symbol)
        if info is None:
            raise MT5DataError(f"No information for {symbol}: {mt5.last_error()}")
        return {
            "symbol": info._asdict(),
            "account_currency": self.currency,
            "margin_mode": self.margin_mode,
            "server": self.server,
            "terminal_build": self.build,
            "exported_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }

    def history_extent(self, symbol: str, first_year: int) -> dict[str, Any]:
        """The first and last M1 bar the terminal holds for a symbol, and how it describes it.

        The first bar is looked for in `first_year` (the oldest yearly history file on disk) and
        the years after it, so nothing older is requested: asking for a symbol's whole daily
        series makes the terminal download every M1 bar the server has, which takes minutes.
        """
        self._select(symbol)
        info = mt5.symbol_info(symbol)
        if info is None:
            raise MT5DataError(f"No information for {symbol}: {mt5.last_error()}")
        first: int | None = None
        this_year = datetime.now(UTC).year
        for year in range(first_year, this_year + 1):
            rates = mt5.copy_rates_range(
                symbol,
                mt5.TIMEFRAME_M1,
                _server_epoch(date(year, 1, 1)),
                _server_epoch(date(year + 1, 1, 1)),
            )
            if rates is not None and len(rates):
                first = int(rates[0]["time"])
                break
        latest = self._settled(lambda: mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M1, 0, 1))
        last = int(latest[-1]["time"]) if latest is not None and len(latest) else None
        return {
            "name": symbol,
            "description": info.description,
            "digits": info.digits,
            "path": info.path,
            "first_bar": first,
            "last_bar": last,
        }

    def measure_clock(self, start: date) -> ServerClock:
        symbol = self.reference_fx_symbol()
        begin = datetime(start.year, start.month, start.day) - CLOCK_LOOKBACK
        frame = self.rates(
            symbol, "H1", begin.date(), (datetime.now(UTC) + timedelta(days=2)).date()
        )
        times = [datetime.fromtimestamp(int(t), UTC).replace(tzinfo=None) for t in frame["time"]]
        return ServerClock.from_bars(self.server, symbol, times)


def _data_config() -> Path:
    """A start configuration for data sessions: more bars per request, no EA, no live trading."""
    path = Path(tempfile.gettempdir()) / "strategylab-data-session.ini"
    text = (
        "[Charts]\r\n"
        f"MaxBars={SESSION_MAX_BARS}\r\n"
        "[Experts]\r\n"
        "AllowLiveTrading=0\r\n"
        "AllowDllImport=0\r\n"
        "Enabled=0\r\n"
    )
    path.write_bytes(codecs.BOM_UTF16_LE + text.encode("utf-16-le"))
    return path


LAUNCH_ATTEMPTS = 3
# A terminal that hands over to another copy, or finds the previous one still closing, exits
# within a second or two; one still running after this long has started for real.
LAUNCH_SETTLE_SECONDS = 5.0


def _launch_for_data(install: TerminalInstall) -> None:
    portable = " /portable" if install.portable else ""
    command = f'"{install.terminal_exe}"{portable} /config:"{_data_config()}"'
    for _ in range(LAUNCH_ATTEMPTS):
        process = subprocess.Popen(command)
        try:
            process.wait(timeout=LAUNCH_SETTLE_SECONDS)
        except subprocess.TimeoutExpired:
            return
        time.sleep(2)
    raise MT5DataError(
        f"{install.terminal_exe} exits straight after starting. Check that it starts by hand "
        "and that no other copy is running from the same folder."
    )


@contextmanager
def open_session(install: TerminalInstall) -> Iterator[TerminalSession]:
    """Attach to the terminal, starting it for the session if it is not running."""
    was_running = bool(terminals_using(install))
    try:
        if not was_running:
            _launch_for_data(install)
        if not mt5.initialize(
            path=str(install.terminal_exe), portable=install.portable, timeout=INIT_TIMEOUT_MS
        ):
            code, message = mt5.last_error()
            raise MT5DataError(
                f"Could not attach to {install.terminal_exe} ({code}: {message}). Check that it "
                "starts by hand and is logged in to an account."
            )
        try:
            data_path = mt5.terminal_info().data_path
            if path_key(data_path) != path_key(install.data_dir):
                raise MT5DataError(
                    f"Attached to a terminal using {data_path}, not {install.data_dir}. Close "
                    "other terminals started from the same folder and try again."
                )
            session = TerminalSession(install)
            if was_running and session.max_bars < SESSION_MAX_BARS:
                raise MT5DataError(
                    f"The terminal is already running, with 'Max bars in chart' at "
                    f"{session.max_bars}, which would cut history short. Close it so StrategyLab "
                    "can start it with its own settings."
                )
            if not was_running and session.max_bars < SESSION_MAX_BARS:
                # The package starts its own copy, without these settings, if ours is not up
                # in time; that copy would silently return only the last ~70 days of M1.
                raise MT5DataError(
                    "The terminal did not start with StrategyLab's data settings. Try again; if "
                    "it keeps happening, close the terminal and any MetaEditor using it."
                )
            yield session
        finally:
            mt5.shutdown()
    finally:
        # The package never stops a terminal, and may have started one itself; the tester needs
        # this data folder to itself afterwards.
        if not was_running:
            for running in terminals_using(install):
                stop_process(running.pid)


def measure_history(
    install: TerminalInstall, first_years: dict[str, int]
) -> tuple[str, list[dict[str, Any]]]:
    """The server name and, for each symbol, its history extent (see history_extent)."""
    with open_session(install) as session:
        found = [session.history_extent(name, year) for name, year in first_years.items()]
        return session.server, found


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


# --- cached exports ----------------------------------------------------------------------------


@dataclass(frozen=True)
class HistoryBundle:
    """Files a simulated run needs, all in server time."""

    server: str
    symbol: str
    m1: Path
    history: Path
    timeframe: str
    spec: Path
    ticks: Path | None
    start: date
    end: date
    warmup_start: date


def _safe(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in name)


def _history_dir(cache_dir: Path, server: str, symbol: str) -> Path:
    return cache_dir / "history" / _safe(server) / _safe(symbol)


def _cacheable(end: date) -> bool:
    # A range reaching today is still growing; caching it would freeze an incomplete day.
    return end <= datetime.now(UTC).date() - timedelta(days=1)


def export_history(
    install: TerminalInstall,
    cache_dir: Path,
    symbol: str,
    timeframe: str,
    start: date,
    end: date,
    *,
    warmup_days: int = 30,
    history_bars: int = 1000,
    with_ticks: bool = False,
) -> HistoryBundle:
    """Export what a simulated run over [start, end) needs, reusing cached files.

    M1 bars cover the run plus `warmup_days` before it, which is what the simulator moves
    through. The run's own timeframe is also exported for `history_bars` bars before the start,
    so a strategy that looks back further than the warm-up still finds history, as it would in
    the Strategy Tester. Real ticks are optional and large.
    """
    if timeframe not in TIMEFRAMES:
        raise MT5DataError(
            f"{timeframe} history cannot be exported; use one of {list(TIMEFRAMES)}."
        )
    warmup_start = start - timedelta(days=warmup_days)
    with open_session(install) as session:
        folder = _history_dir(cache_dir, session.server, symbol)
        folder.mkdir(parents=True, exist_ok=True)
        keep = _cacheable(end)

        def cached(path: Path, produce: Any) -> Path:
            if keep and path.is_file():
                return path
            frame = produce()
            temp = path.with_suffix(".tmp")
            frame.to_parquet(temp, index=False)
            temp.replace(path)
            return path

        m1 = cached(
            folder / f"M1_{warmup_start:%Y%m%d}_{end:%Y%m%d}.parquet",
            lambda: session.rates(symbol, "M1", warmup_start, end),
        )
        history = cached(
            folder / f"{timeframe}_before_{warmup_start:%Y%m%d}_{history_bars}.parquet",
            lambda: session.rates_before(symbol, timeframe, warmup_start, history_bars),
        )
        ticks = None
        if with_ticks:
            ticks = cached(
                folder / f"ticks_{start:%Y%m%d}_{end:%Y%m%d}.parquet",
                lambda: session.ticks(symbol, start, end),
            )
        spec = folder / "spec.json"
        spec.write_text(json.dumps(session.symbol_spec(symbol), indent=1), encoding="utf-8")
        server = session.server
    return HistoryBundle(
        server=server,
        symbol=symbol,
        m1=m1,
        history=history,
        timeframe=timeframe,
        spec=spec,
        ticks=ticks,
        start=start,
        end=end,
        warmup_start=warmup_start,
    )

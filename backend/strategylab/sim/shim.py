"""Stand-in MetaTrader5 module served to strategies inside the simulator subprocess.

Registered as sys.modules["MetaTrader5"] before the strategy runs, so an unmodified script that
does `import MetaTrader5 as mt5` talks to the simulator. The functions, record types, array
layouts and constants are those of the real package (see constants.py, checked by a test).
Anything else raises SimulatorUnsupportedError naming what was asked for; nothing returns None
silently in place of an answer.

Inside the simulation the run's symbol also answers to its name without the broker's suffix,
so a script written for EURUSD runs on a server that calls it EURUSD@ or EURUSD.m. Times passed
in are read as server time (naive datetimes and plain numbers alike), and anything later than
the simulated present is cut back to it.
"""

from __future__ import annotations

import fnmatch
import re
import sys
from collections import namedtuple
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from types import ModuleType
from typing import Any

import numpy as np

from strategylab.sim.broker import BUY, Broker, Order, Position, SimulatorUnsupportedError
from strategylab.sim.clock import SimClock
from strategylab.sim.constants import CONSTANTS, RECORD_FIELDS
from strategylab.sim.market import MONTHLY, TIMEFRAME_MS, Market

__version__ = "5.0.6231"

RECORDS = {name: namedtuple(name, fields) for name, fields in RECORD_FIELDS.items()}
AccountInfo = RECORDS["AccountInfo"]
TerminalInfo = RECORDS["TerminalInfo"]
SymbolInfo = RECORDS["SymbolInfo"]
Tick = RECORDS["Tick"]
TradeOrder = RECORDS["TradeOrder"]
TradePosition = RECORDS["TradePosition"]
TradeDeal = RECORDS["TradeDeal"]
TradeRequest = RECORDS["TradeRequest"]
OrderSendResult = RECORDS["OrderSendResult"]
OrderCheckResult = RECORDS["OrderCheckResult"]

RES_SUCCESS = (CONSTANTS["RES_S_OK"], "Success")
RES_INVALID = (CONSTANTS["RES_E_INVALID_PARAMS"], "Invalid params")
RES_NOT_FOUND = (CONSTANTS["RES_E_NOT_FOUND"], "No data")
# Market and session statistics in SymbolInfo describe the moment the symbol was exported, which
# is in the simulation's future; they are replaced by simulated values or zero.
LIVE_SYMBOL_FIELDS = {
    name
    for name in RECORD_FIELDS["SymbolInfo"]
    if name.startswith(("session_", "price_", "bid", "ask", "last", "volumehigh", "volumelow"))
    or name in ("time", "spread", "volume", "volume_real")
}
TIMEFRAMES = {value for name, value in CONSTANTS.items() if name.startswith("TIMEFRAME_")}


def _record(kind: Any, values: dict[str, Any]) -> Any:
    return kind(**{field: values.get(field, 0) for field in kind._fields})


class TerminalStandIn:
    """The simulated terminal behind the module functions."""

    def __init__(self, broker: Broker, clock: SimClock, market: Market, build: int) -> None:
        self.broker = broker
        self.clock = clock
        self.market = market
        self.build = build
        self.symbol = broker.symbol
        # The name without a broker suffix: EURUSD for EURUSD@ or EURUSD.m.
        stem = re.match(r"[A-Za-z0-9]+", self.symbol)
        self.alias = stem.group(0) if stem else self.symbol
        self.error: tuple[int, str] = RES_SUCCESS

    # --- helpers -------------------------------------------------------------------------------

    def call(self) -> None:
        self.clock.touch()
        self.error = RES_SUCCESS

    def resolve(self, symbol: str, function: str) -> str:
        if symbol == self.symbol or symbol.upper() in (self.alias.upper(), self.symbol.upper()):
            return self.symbol
        raise SimulatorUnsupportedError(
            f"{function}: only {self.symbol} is available in this simulation; the strategy "
            f"asked for {symbol!r}. Python strategies are simulated on one symbol."
        )

    def matches(self, group: str | None) -> bool:
        if not group:
            return True
        included = False
        for pattern in (p.strip() for p in group.split(",") if p.strip()):
            if pattern.startswith("!"):
                if fnmatch.fnmatchcase(self.symbol, pattern[1:]):
                    return False
            elif fnmatch.fnmatchcase(self.symbol, pattern):
                included = True
        return included

    @staticmethod
    def to_ms(moment: Any) -> int:
        if isinstance(moment, datetime):
            aware = moment if moment.tzinfo else moment.replace(tzinfo=UTC)
            return int(aware.timestamp() * 1000)
        return int(float(moment) * 1000)

    @property
    def index(self) -> int:
        return self.broker.index

    # --- session -------------------------------------------------------------------------------

    def account_info(self) -> Any:
        broker = self.broker
        equity, margin = broker.equity, broker.margin
        values = {
            "login": 0,
            "trade_mode": CONSTANTS["ACCOUNT_TRADE_MODE_DEMO"],
            "leverage": broker.account.leverage,
            "margin_so_mode": CONSTANTS["ACCOUNT_STOPOUT_MODE_PERCENT"],
            "trade_allowed": True,
            "trade_expert": True,
            "margin_mode": broker.account.margin_mode,
            "currency_digits": 2,
            "fifo_close": False,
            "balance": broker.balance,
            "credit": 0.0,
            "profit": round(equity - broker.balance, 2),
            "equity": equity,
            "margin": margin,
            "margin_free": round(equity - margin, 2),
            "margin_level": round(equity / margin * 100, 2) if margin else 0.0,
            "margin_so_call": 50.0,
            "margin_so_so": 30.0,
            "name": "StrategyLab simulation",
            "server": "StrategyLab simulator",
            "currency": broker.account.currency,
            "company": "StrategyLab",
        }
        return _record(AccountInfo, values)

    def terminal_info(self) -> Any:
        values = {
            "connected": True,
            "trade_allowed": True,
            "tradeapi_disabled": False,
            "dlls_allowed": False,
            "build": self.build,
            "maxbars": 100_000_000,
            "company": "StrategyLab",
            "name": "StrategyLab simulator",
            "language": "English",
            "path": "",
            "data_path": "",
            "commondata_path": "",
        }
        return _record(TerminalInfo, values)

    def symbol_info(self) -> Any:
        values = {k: v for k, v in self.broker.spec.items() if k not in LIVE_SYMBOL_FIELDS}
        values.update(dict.fromkeys(LIVE_SYMBOL_FIELDS, 0))
        values["select"] = values["visible"] = True
        if self.index >= 0:
            day = self.market.bars_at(CONSTANTS["TIMEFRAME_D1"], self.index)
            today = day.forming
            values.update(
                time=self.broker.price_time_ms // 1000,
                bid=self.broker.bid,
                ask=self.broker.ask,
                spread=round((self.broker.ask - self.broker.bid) / self.broker.point),
                bidhigh=float(today["high"]),
                bidlow=float(today["low"]),
                askhigh=float(today["high"]) + self.broker.ask - self.broker.bid,
                asklow=float(today["low"]) + self.broker.ask - self.broker.bid,
                session_open=float(today["open"]),
                session_close=float(day.complete["close"][-1]) if len(day.complete) else 0.0,
            )
        return _record(SymbolInfo, values)

    def tick(self) -> Any:
        if self.index < 0:
            return None
        stream = self.market.stream
        ms = int(stream.time_ms[self.index])
        return Tick(
            time=ms // 1000,
            bid=float(stream.bid[self.index]),
            ask=float(stream.ask[self.index]),
            last=float(stream.last[self.index]),
            volume=int(stream.volume[self.index]),
            time_msc=ms,
            flags=int(stream.flags[self.index]),
            volume_real=float(stream.volume_real[self.index]),
        )

    # --- history -------------------------------------------------------------------------------

    def _bars(self, timeframe: int) -> Any:
        if timeframe not in TIMEFRAMES or (timeframe not in TIMEFRAME_MS and timeframe != MONTHLY):
            self.error = RES_INVALID
            return None
        return self.market.bars_at(timeframe, self.index)

    def _answer(self, rates: np.ndarray) -> np.ndarray | None:
        if not len(rates):
            self.error = RES_NOT_FOUND
            return None
        return rates

    def rates_from(self, timeframe: int, date_from: Any, count: int) -> np.ndarray | None:
        view = self._bars(timeframe)
        if view is None:
            return None
        until = min(self.to_ms(date_from), self.clock.now_ms) // 1000
        stop = view.count_until(until)
        return self._answer(view.slice(stop - int(count), stop))

    def rates_from_pos(self, timeframe: int, start_pos: int, count: int) -> np.ndarray | None:
        view = self._bars(timeframe)
        if view is None:
            return None
        stop = len(view) - int(start_pos)
        return self._answer(view.slice(stop - int(count), stop))

    def rates_range(self, timeframe: int, date_from: Any, date_to: Any) -> np.ndarray | None:
        view = self._bars(timeframe)
        if view is None:
            return None
        start = view.count_until(self.to_ms(date_from) // 1000 - 1)
        stop = view.count_until(min(self.to_ms(date_to), self.clock.now_ms) // 1000)
        return self._answer(view.slice(start, stop))

    def _tick_window(self, start: int, stop: int, flags: int) -> np.ndarray | None:
        ticks = self.market.ticks(start, stop)
        if flags == CONSTANTS["COPY_TICKS_TRADE"]:
            ticks = ticks[(ticks["flags"] & CONSTANTS["TICK_FLAG_LAST"]) != 0]
        if not len(ticks):
            self.error = RES_NOT_FOUND
            return None
        return ticks

    def ticks_from(self, date_from: Any, count: int, flags: int) -> np.ndarray | None:
        times = self.market.stream.time_ms
        start = int(np.searchsorted(times, self.to_ms(date_from), side="left"))
        stop = min(self.index + 1, start + int(count))
        return self._tick_window(start, stop, flags)

    def ticks_range(self, date_from: Any, date_to: Any, flags: int) -> np.ndarray | None:
        times = self.market.stream.time_ms
        start = int(np.searchsorted(times, self.to_ms(date_from), side="left"))
        stop = int(
            np.searchsorted(times, min(self.to_ms(date_to), self.clock.now_ms), side="right")
        )
        return self._tick_window(start, min(stop, self.index + 1), flags)

    # --- trading -------------------------------------------------------------------------------

    def request(self, raw: dict[str, Any], function: str) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise TypeError(f"{function}: the request must be a dict, got {type(raw).__name__}")
        unknown = set(raw) - set(TradeRequest._fields)
        if unknown:
            raise SimulatorUnsupportedError(
                f"{function}: unknown request field(s) {', '.join(sorted(unknown))}."
            )
        request = {field: raw.get(field, 0) for field in TradeRequest._fields}
        request["comment"] = raw.get("comment", "") or ""
        request["symbol"] = self.resolve(raw.get("symbol", self.symbol), function)
        return request

    def send(self, raw: dict[str, Any]) -> Any:
        request = self.request(raw, "order_send")
        outcome = self.broker.send(request)
        return OrderSendResult(
            retcode=outcome.retcode,
            deal=outcome.deal,
            order=outcome.order,
            volume=outcome.volume,
            price=outcome.price,
            bid=outcome.bid,
            ask=outcome.ask,
            comment=outcome.comment,
            request_id=0,
            retcode_external=0,
            request=TradeRequest(**request),
        )

    def check(self, raw: dict[str, Any]) -> Any:
        request = self.request(raw, "order_check")
        retcode, margin = self.broker.check(request)
        equity = self.broker.equity
        margin_total = self.broker.margin + margin
        return OrderCheckResult(
            retcode=retcode,
            balance=self.broker.balance,
            equity=equity,
            profit=round(equity - self.broker.balance, 2),
            margin=round(margin, 2),
            margin_free=round(equity - margin_total, 2),
            margin_level=round(equity / margin_total * 100, 2) if margin_total else 0.0,
            comment="Done" if retcode == 0 else "Rejected",
            request=TradeRequest(**request),
        )

    def position_record(self, p: Position) -> Any:
        price = self.broker.bid if p.type == BUY else self.broker.ask
        return TradePosition(
            ticket=p.ticket,
            time=p.time_ms // 1000,
            time_msc=p.time_ms,
            time_update=p.time_update_ms // 1000,
            time_update_msc=p.time_update_ms,
            type=p.type,
            magic=p.magic,
            identifier=p.ticket,
            reason=p.reason,
            volume=p.volume,
            price_open=p.price_open,
            sl=p.sl,
            tp=p.tp,
            price_current=price,
            swap=p.swap,
            profit=round(self.broker.floating_profit(p), 2),
            symbol=self.symbol,
            comment=p.comment,
            external_id="",
        )

    def order_record(self, o: Order) -> Any:
        buy = o.type in (BUY, CONSTANTS["ORDER_TYPE_BUY_LIMIT"], CONSTANTS["ORDER_TYPE_BUY_STOP"])
        return TradeOrder(
            ticket=o.ticket,
            time_setup=o.time_setup_ms // 1000,
            time_setup_msc=o.time_setup_ms,
            time_done=o.time_done_ms // 1000,
            time_done_msc=o.time_done_ms,
            time_expiration=o.time_expiration_ms // 1000,
            type=o.type,
            type_time=o.type_time,
            type_filling=o.type_filling,
            state=o.state,
            magic=o.magic,
            position_id=o.position_id,
            position_by_id=0,
            reason=o.reason,
            volume_initial=o.volume_initial,
            volume_current=o.volume_current,
            price_open=o.price_open,
            sl=o.sl,
            tp=o.tp,
            price_current=self.broker.ask if buy else self.broker.bid,
            price_stoplimit=0.0,
            symbol=self.symbol,
            comment=o.comment,
            external_id="",
        )

    def deal_record(self, d: Any) -> Any:
        return TradeDeal(
            ticket=d.ticket,
            order=d.order,
            time=d.time_ms // 1000,
            time_msc=d.time_ms,
            type=d.type,
            entry=d.entry,
            magic=d.magic,
            position_id=d.position_id,
            reason=d.reason,
            volume=d.volume,
            price=d.price,
            commission=d.commission,
            swap=d.swap,
            profit=d.profit,
            fee=d.fee,
            symbol=d.symbol,
            comment=d.comment,
            external_id="",
        )

    def select(
        self,
        items: Iterable[Any],
        *,
        symbol: str | None,
        group: str | None,
        ticket: int | None,
        function: str,
    ) -> list[Any]:
        if symbol is not None:
            self.resolve(symbol, function)
        if not self.matches(group):
            return []
        chosen = list(items)
        if ticket is not None:
            chosen = [item for item in chosen if item.ticket == int(ticket)]
        return chosen

    def history(
        self,
        items: Iterable[Any],
        time_of: Callable[[Any], int],
        args: tuple[Any, ...],
        group: str | None,
        ticket: int | None,
        position: int | None,
    ) -> list[Any]:
        chosen = [item for item in items if time_of(item) <= self.clock.now_ms]
        if len(args) >= 2:
            low, high = self.to_ms(args[0]), self.to_ms(args[1])
            chosen = [item for item in chosen if low <= time_of(item) <= high]
        if not self.matches(group):
            return []
        if ticket is not None:
            chosen = [item for item in chosen if item.ticket == int(ticket)]
        if position is not None:
            chosen = [
                item for item in chosen if getattr(item, "position_id", None) == int(position)
            ]
        return chosen


_terminal: TerminalStandIn | None = None


def _active(function: str) -> TerminalStandIn:
    if _terminal is None:
        raise SimulatorUnsupportedError(f"{function}: the simulator has not been started.")
    _terminal.call()
    return _terminal


def install(terminal: TerminalStandIn) -> ModuleType:
    """Serve this terminal as the MetaTrader5 module of the current process."""
    global _terminal
    _terminal = terminal
    module = sys.modules[__name__]
    for name, value in CONSTANTS.items():
        setattr(module, name, value)
    sys.modules["MetaTrader5"] = module
    return module


def __getattr__(name: str) -> Any:
    if name.startswith("__"):
        raise AttributeError(name)
    raise SimulatorUnsupportedError(
        f"MetaTrader5.{name} is not supported by the StrategyLab simulator."
    )


# --- the module's functions, named and shaped as in the MetaTrader5 package --------------------


def initialize(*args: Any, **kwargs: Any) -> bool:
    _active("initialize")
    return True


def login(*args: Any, **kwargs: Any) -> bool:
    _active("login")
    return True


def shutdown() -> None:
    _active("shutdown")


def version() -> tuple[int, int, str]:
    terminal = _active("version")
    return (500, terminal.build, "09 Oct 2026")


def last_error() -> tuple[int, str]:
    if _terminal is None:
        return RES_SUCCESS
    return _terminal.error


def account_info() -> Any:
    return _active("account_info").account_info()


def terminal_info() -> Any:
    return _active("terminal_info").terminal_info()


def symbols_total() -> int:
    _active("symbols_total")
    return 1


def symbols_get(group: str | None = None) -> tuple[Any, ...]:
    terminal = _active("symbols_get")
    return (terminal.symbol_info(),) if terminal.matches(group) else ()


def symbol_info(symbol: str) -> Any:
    terminal = _active("symbol_info")
    terminal.resolve(symbol, "symbol_info")
    return terminal.symbol_info()


def symbol_info_tick(symbol: str) -> Any:
    terminal = _active("symbol_info_tick")
    terminal.resolve(symbol, "symbol_info_tick")
    return terminal.tick()


def symbol_select(symbol: str, enable: bool = True) -> bool:
    terminal = _active("symbol_select")
    terminal.resolve(symbol, "symbol_select")
    return True


def copy_rates_from(symbol: str, timeframe: int, date_from: Any, count: int) -> np.ndarray | None:
    terminal = _active("copy_rates_from")
    terminal.resolve(symbol, "copy_rates_from")
    return terminal.rates_from(int(timeframe), date_from, count)


def copy_rates_from_pos(
    symbol: str, timeframe: int, start_pos: int, count: int
) -> np.ndarray | None:
    terminal = _active("copy_rates_from_pos")
    terminal.resolve(symbol, "copy_rates_from_pos")
    return terminal.rates_from_pos(int(timeframe), start_pos, count)


def copy_rates_range(
    symbol: str, timeframe: int, date_from: Any, date_to: Any
) -> np.ndarray | None:
    terminal = _active("copy_rates_range")
    terminal.resolve(symbol, "copy_rates_range")
    return terminal.rates_range(int(timeframe), date_from, date_to)


def copy_ticks_from(symbol: str, date_from: Any, count: int, flags: int) -> np.ndarray | None:
    terminal = _active("copy_ticks_from")
    terminal.resolve(symbol, "copy_ticks_from")
    return terminal.ticks_from(date_from, count, int(flags))


def copy_ticks_range(symbol: str, date_from: Any, date_to: Any, flags: int) -> np.ndarray | None:
    terminal = _active("copy_ticks_range")
    terminal.resolve(symbol, "copy_ticks_range")
    return terminal.ticks_range(date_from, date_to, int(flags))


def order_calc_margin(action: int, symbol: str, volume: float, price: float) -> float:
    terminal = _active("order_calc_margin")
    terminal.resolve(symbol, "order_calc_margin")
    return round(terminal.broker.calc_margin(int(action), float(volume), float(price)), 2)


def order_calc_profit(
    action: int, symbol: str, volume: float, price_open: float, price_close: float
) -> float:
    terminal = _active("order_calc_profit")
    terminal.resolve(symbol, "order_calc_profit")
    profit = terminal.broker.calc_profit(
        int(action), float(volume), float(price_open), float(price_close)
    )
    return round(profit, 2)


def order_check(request: dict[str, Any]) -> Any:
    return _active("order_check").check(request)


def order_send(request: dict[str, Any]) -> Any:
    return _active("order_send").send(request)


def positions_total() -> int:
    return len(_active("positions_total").broker.positions)


def positions_get(
    symbol: str | None = None, group: str | None = None, ticket: int | None = None
) -> tuple[Any, ...]:
    terminal = _active("positions_get")
    chosen = terminal.select(
        terminal.broker.positions,
        symbol=symbol,
        group=group,
        ticket=ticket,
        function="positions_get",
    )
    return tuple(terminal.position_record(p) for p in chosen)


def orders_total() -> int:
    return len(_active("orders_total").broker.orders)


def orders_get(
    symbol: str | None = None, group: str | None = None, ticket: int | None = None
) -> tuple[Any, ...]:
    terminal = _active("orders_get")
    chosen = terminal.select(
        terminal.broker.orders, symbol=symbol, group=group, ticket=ticket, function="orders_get"
    )
    return tuple(terminal.order_record(o) for o in chosen)


def history_orders_total(date_from: Any, date_to: Any) -> int:
    return len(history_orders_get(date_from, date_to))


def history_orders_get(
    *args: Any, group: str | None = None, ticket: int | None = None, position: int | None = None
) -> tuple[Any, ...]:
    terminal = _active("history_orders_get")
    chosen = terminal.history(
        terminal.broker.history_orders, lambda o: o.time_setup_ms, args, group, ticket, position
    )
    return tuple(terminal.order_record(o) for o in chosen)


def history_deals_total(date_from: Any, date_to: Any) -> int:
    return len(history_deals_get(date_from, date_to))


def history_deals_get(
    *args: Any, group: str | None = None, ticket: int | None = None, position: int | None = None
) -> tuple[Any, ...]:
    terminal = _active("history_deals_get")
    chosen = terminal.history(
        terminal.broker.deals, lambda d: d.time_ms, args, group, ticket, position
    )
    return tuple(terminal.deal_record(d) for d in chosen)

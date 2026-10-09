"""Simulated account, order fills and deal recording.

Fill assumptions:

- A market order fills at once at the current price: buys at the ask, sells at the bid. The
  requested price and deviation are ignored, as under market execution, and there is no
  slippage. Ask is bid plus the spread recorded for that minute, or the recorded ask when
  running on real ticks.
- Stop loss and take profit are checked at every price of the stream: a long position's levels
  against the bid, a short's against the ask. On 1-minute bars a reached level closes the
  position at the level, even when the price gapped beyond it, which is what the Strategy
  Tester's "1 minute OHLC" mode does (every one of its stop and target exits in the parity
  study was at the level). On real ticks the position closes at the price of the tick that
  crossed the level, as the tester's real-tick mode does.
- When stop loss and take profit both fall inside one minute, the order of the minute's prices
  decides: open, low, high, close on a rising bar and open, high, low, close on a falling one,
  as in the Strategy Tester's "1 minute OHLC" mode.
- Limit orders fill at their price. Stop orders fill at their price on 1-minute bars and at
  the crossing tick's price on real ticks. Stop-limit orders and close-by are not simulated.
- Commission is a fixed amount per lot charged on every deal, zero unless set. Swap is charged
  at each server midnight that ends a weekday (triple on the symbol's three-day-swap day), from
  the symbol's current swap settings, in points or in money; interest-based swap modes are not
  simulated. Profit in a currency other than the deposit's is converted at the closing price
  when the deposit currency is the symbol's base currency, otherwise with the symbol's tick
  value as exported.
- Margin is checked when a position is opened or a pending order fills; stop-out is not
  simulated. The fill policy, volume limits and stops level are enforced as the tester does.
- Trading sessions: a request is accepted only when the present lies inside one of the
  symbol's trade sessions and a price has arrived since that session opened (so not on a
  holiday, and not before the run's first price); otherwise it is rejected with "Market closed".
  Stops and pending orders wait for the session to open, as in the tester. Only the forced
  close at the end of the run ignores sessions.
- Swap is converted to the deposit currency at the last price before midnight, which is what
  the tester does (seen in the cents of USDJPY swaps).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np

from strategylab.sim.constants import CONSTANTS as C
from strategylab.sim.market import DAY_MS, Market

DONE = C["TRADE_RETCODE_DONE"]
PLACED = C["TRADE_RETCODE_PLACED"]
BUY, SELL = C["ORDER_TYPE_BUY"], C["ORDER_TYPE_SELL"]
BUY_LIMIT, SELL_LIMIT = C["ORDER_TYPE_BUY_LIMIT"], C["ORDER_TYPE_SELL_LIMIT"]
BUY_STOP, SELL_STOP = C["ORDER_TYPE_BUY_STOP"], C["ORDER_TYPE_SELL_STOP"]
PENDING_TYPES = (BUY_LIMIT, SELL_LIMIT, BUY_STOP, SELL_STOP)
STOP_LIMIT_TYPES = (C["ORDER_TYPE_BUY_STOP_LIMIT"], C["ORDER_TYPE_SELL_STOP_LIMIT"])
HEDGING = C["ACCOUNT_MARGIN_MODE_RETAIL_HEDGING"]
SYMBOL_FILLING_FOK, SYMBOL_FILLING_IOC = 1, 2
EXECUTION_MARKET = C["SYMBOL_TRADE_EXECUTION_MARKET"]
MESSAGES = {
    DONE: "Request executed",
    PLACED: "Request placed",
    C["TRADE_RETCODE_INVALID"]: "Invalid request",
    C["TRADE_RETCODE_INVALID_VOLUME"]: "Invalid volume",
    C["TRADE_RETCODE_INVALID_PRICE"]: "Invalid price",
    C["TRADE_RETCODE_INVALID_STOPS"]: "Invalid stops",
    C["TRADE_RETCODE_NO_MONEY"]: "No money",
    C["TRADE_RETCODE_PRICE_OFF"]: "No prices",
    C["TRADE_RETCODE_INVALID_FILL"]: "Unsupported filling mode",
    C["TRADE_RETCODE_POSITION_CLOSED"]: "Position doesn't exist",
    C["TRADE_RETCODE_INVALID_ORDER"]: "Invalid order",
    C["TRADE_RETCODE_INVALID_EXPIRATION"]: "Invalid expiration",
    C["TRADE_RETCODE_NO_CHANGES"]: "No changes",
    C["TRADE_RETCODE_MARKET_CLOSED"]: "Market closed",
}
# MetaTrader numbers ENUM_DAY_OF_WEEK from Sunday = 0; Python's weekday() from Monday = 0.
MQL_TO_PY_WEEKDAY = {0: 6, 1: 0, 2: 1, 3: 2, 4: 3, 5: 4, 6: 5}


class SimulatorUnsupportedError(NotImplementedError):
    """The strategy asked for something the simulator does not do; the message names it."""


@dataclass
class AccountSettings:
    deposit: float
    currency: str
    leverage: int
    margin_mode: int = HEDGING
    commission_per_lot: float = 0.0


@dataclass
class Position:
    ticket: int
    type: int
    volume: float
    price_open: float
    sl: float
    tp: float
    time_ms: int
    time_update_ms: int
    magic: int
    comment: str
    reason: int
    swap: float = 0.0


@dataclass
class Order:
    ticket: int
    type: int
    volume_initial: float
    volume_current: float
    price_open: float
    sl: float
    tp: float
    time_setup_ms: int
    type_time: int
    type_filling: int
    time_expiration_ms: int
    magic: int
    comment: str
    reason: int
    state: int = C["ORDER_STATE_PLACED"]
    time_done_ms: int = 0
    position_id: int = 0


@dataclass
class DealRecord:
    ticket: int
    order: int
    time_ms: int
    type: int
    entry: int
    magic: int
    position_id: int
    reason: int
    volume: float
    price: float
    commission: float
    swap: float
    profit: float
    symbol: str
    comment: str
    balance: float
    fee: float = 0.0


@dataclass
class Outcome:
    retcode: int
    deal: int = 0
    order: int = 0
    volume: float = 0.0
    price: float = 0.0
    bid: float = 0.0
    ask: float = 0.0
    comment: str = ""

    def __post_init__(self) -> None:
        if not self.comment:
            self.comment = MESSAGES.get(self.retcode, "")


def _money(value: float) -> float:
    return round(value + 0.0, 2)


def _is_step(volume: float, step: float) -> bool:
    if step <= 0:
        return True
    ratio = volume / step
    return abs(ratio - round(ratio)) < 1e-6


@dataclass
class Broker:
    market: Market
    spec: dict[str, Any]
    account: AccountSettings
    start_ms: int
    index: int = -1
    balance: float = 0.0
    positions: list[Position] = field(default_factory=list)
    orders: list[Order] = field(default_factory=list)
    history_orders: list[Order] = field(default_factory=list)
    deals: list[DealRecord] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    trade_sessions: dict[int, list[tuple[int, int]]] | None = None
    """Trade session windows per MQL5 weekday (0 = Sunday), seconds after midnight."""
    now_ms: int = 0
    """The simulated present, which can be later than the last price."""
    _next_order: int = 2
    _next_deal: int = 2
    _day: int = 0

    def __post_init__(self) -> None:
        s = self.spec
        self.symbol: str = s["name"]
        self.digits: int = int(s["digits"])
        self.point: float = float(s["point"])
        self.tick_size: float = float(s["trade_tick_size"]) or self.point
        self.tick_value: float = float(s["trade_tick_value"])
        self.contract: float = float(s["trade_contract_size"])
        self.index = self.market.index_at(self.start_ms - 1)
        self.now_ms = self.start_ms
        self._day = self.start_ms // DAY_MS
        self._tradable = self._session_mask()
        self.balance = _money(self.account.deposit)
        self.deals.append(
            DealRecord(
                ticket=1,
                order=0,
                time_ms=self.start_ms,
                type=C["DEAL_TYPE_BALANCE"],
                entry=C["DEAL_ENTRY_IN"],
                magic=0,
                position_id=0,
                reason=C["DEAL_REASON_CLIENT"],
                volume=0.0,
                price=0.0,
                commission=0.0,
                swap=0.0,
                profit=self.balance,
                symbol="",
                comment="",
                balance=self.balance,
            )
        )
        swap_mode = int(s.get("swap_mode", 0))
        if swap_mode not in (
            C["SYMBOL_SWAP_MODE_DISABLED"],
            C["SYMBOL_SWAP_MODE_POINTS"],
            C["SYMBOL_SWAP_MODE_CURRENCY_SYMBOL"],
            C["SYMBOL_SWAP_MODE_CURRENCY_MARGIN"],
            C["SYMBOL_SWAP_MODE_CURRENCY_DEPOSIT"],
        ):
            self.notes.append(
                f"{self.symbol} charges swap by interest (mode {swap_mode}), which is not "
                "simulated: no swap was charged."
            )

    def _session_mask(self) -> np.ndarray:
        times = self.market.stream.time_ms // 1000
        if self.trade_sessions is None:
            self.notes.append(
                "The symbol's trading sessions were not known, so orders were accepted at any "
                "time prices were quoted."
            )
            return np.ones(len(times), dtype=bool)
        # 1970-01-01 was a Thursday, which MQL5 numbers 4 (Sunday is 0).
        weekday = (times // 86_400 + 4) % 7
        second = times % 86_400
        mask = np.zeros(len(times), dtype=bool)
        for day, windows in self.trade_sessions.items():
            for start, end in windows:
                mask |= (weekday == int(day)) & (second >= start) & (second < end)
        return mask

    def _session_start_ms(self, moment_ms: int) -> int | None:
        """Start of the trade session containing `moment_ms`, or None if trading is closed."""
        midnight = moment_ms - moment_ms % DAY_MS
        if self.trade_sessions is None:
            return midnight
        weekday = (moment_ms // DAY_MS + 4) % 7
        second = (moment_ms - midnight) // 1000
        for start, end in self.trade_sessions.get(int(weekday), []):
            if start <= second < end:
                return midnight + start * 1000
        return None

    @property
    def market_open(self) -> bool:
        if self.index < 0:
            return False
        last_price_ms = self.price_time_ms
        session_start = self._session_start_ms(self.now_ms)
        # A price must have arrived during this session and within the run: an order cannot be
        # filled at Friday's close on a Monday holiday, or at a price from before the start.
        return session_start is not None and last_price_ms >= max(session_start, self.start_ms)

    # --- prices --------------------------------------------------------------------------------

    @property
    def has_price(self) -> bool:
        return self.index >= 0

    @property
    def bid(self) -> float:
        return float(self.market.stream.bid[self.index]) if self.index >= 0 else 0.0

    @property
    def ask(self) -> float:
        return float(self.market.stream.ask[self.index]) if self.index >= 0 else 0.0

    @property
    def price_time_ms(self) -> int:
        return int(self.market.stream.time_ms[self.index]) if self.index >= 0 else self.start_ms

    def _round_price(self, price: float) -> float:
        return round(price, self.digits)

    # Prices arrive from the terminal with binary noise (an ask of 1.0866500000000001 for
    # 1.08665), so a level counts as reached when the price is within half a point of it,
    # which is comparing both at the symbol's digits.
    def _at_or_below(self, prices: Any, level: float) -> Any:
        return prices < level + self.point / 2

    def _at_or_above(self, prices: Any, level: float) -> Any:
        return prices > level - self.point / 2

    # --- money ---------------------------------------------------------------------------------

    def _to_account(self, amount_profit_ccy: float, price: float) -> float:
        s = self.spec
        account = self.account.currency.upper()
        if s.get("currency_profit", "").upper() == account:
            return amount_profit_ccy
        if s.get("currency_base", "").upper() == account and price > 0:
            return amount_profit_ccy / price
        # Neither currency is the deposit's: use the exported tick value, a fixed rate.
        per_unit = self.tick_value / (self.tick_size * self.contract) if self.contract else 0.0
        return amount_profit_ccy * per_unit

    def calc_profit(
        self, order_type: int, volume: float, price_open: float, price_close: float
    ) -> float:
        direction = 1 if order_type in (BUY, BUY_LIMIT, BUY_STOP) else -1
        raw = (price_close - price_open) * direction * volume * self.contract
        return self._to_account(raw, price_close)

    def calc_margin(self, order_type: int, volume: float, price: float) -> float:
        s = self.spec
        mode = int(s.get("trade_calc_mode", 0))
        leverage = max(1, self.account.leverage)
        account = self.account.currency.upper()
        units = volume * self.contract
        if mode in (C["SYMBOL_CALC_MODE_FOREX"], C["SYMBOL_CALC_MODE_FOREX_NO_LEVERAGE"]):
            divisor = leverage if mode == C["SYMBOL_CALC_MODE_FOREX"] else 1
            margin_ccy = s.get("currency_margin", s.get("currency_base", "")).upper()
            if margin_ccy == account:
                return units / divisor
            return self._to_account(units * price, price) / divisor
        # CFDs, futures and exchange instruments: notional value over leverage, an approximation.
        return self._to_account(units * price, price) / leverage

    def floating_profit(self, position: Position) -> float:
        price = self.bid if position.type == BUY else self.ask
        return self.calc_profit(position.type, position.volume, position.price_open, price)

    @property
    def equity(self) -> float:
        return _money(self.balance + sum(self.floating_profit(p) + p.swap for p in self.positions))

    @property
    def margin(self) -> float:
        return _money(sum(self.calc_margin(p.type, p.volume, p.price_open) for p in self.positions))

    def _commission(self, volume: float) -> float:
        return _money(-abs(self.account.commission_per_lot) * volume)

    # --- deal and order records ----------------------------------------------------------------

    def _new_order_ticket(self) -> int:
        ticket = self._next_order
        self._next_order += 1
        return ticket

    def _record_deal(
        self,
        *,
        order: int,
        deal_type: int,
        entry: int,
        position_id: int,
        volume: float,
        price: float,
        profit: float,
        swap: float,
        magic: int,
        reason: int,
        comment: str,
    ) -> DealRecord:
        commission = self._commission(volume)
        profit, swap = _money(profit), _money(swap)
        self.balance = _money(self.balance + profit + swap + commission)
        deal = DealRecord(
            ticket=self._next_deal,
            order=order,
            time_ms=self.price_time_ms,
            type=deal_type,
            entry=entry,
            magic=magic,
            position_id=position_id,
            reason=reason,
            volume=volume,
            price=price,
            commission=commission,
            swap=swap,
            profit=profit,
            symbol=self.symbol,
            comment=comment,
            balance=self.balance,
        )
        self._next_deal += 1
        self.deals.append(deal)
        return deal

    def _filled_order(
        self,
        order_type: int,
        volume: float,
        price: float,
        request: dict[str, Any],
        position_id: int,
    ) -> Order:
        ticket = self._new_order_ticket()
        order = Order(
            ticket=ticket,
            type=order_type,
            volume_initial=volume,
            volume_current=0.0,
            price_open=price,
            sl=float(request.get("sl", 0.0) or 0.0),
            tp=float(request.get("tp", 0.0) or 0.0),
            time_setup_ms=self.price_time_ms,
            type_time=int(request.get("type_time", C["ORDER_TIME_GTC"])),
            type_filling=int(request.get("type_filling", C["ORDER_FILLING_FOK"])),
            time_expiration_ms=0,
            magic=int(request.get("magic", 0)),
            comment=str(request.get("comment", "")),
            reason=C["ORDER_REASON_EXPERT"],
            state=C["ORDER_STATE_FILLED"],
            time_done_ms=self.price_time_ms,
            position_id=position_id or ticket,
        )
        self.history_orders.append(order)
        return order

    # --- validation ----------------------------------------------------------------------------

    def _volume_ok(self, volume: float) -> bool:
        s = self.spec
        return (
            volume >= float(s["volume_min"]) - 1e-9
            and volume <= float(s["volume_max"]) + 1e-9
            and _is_step(volume, float(s["volume_step"]))
        )

    def _filling_ok(self, filling: int) -> bool:
        flags = int(self.spec.get("filling_mode", 0))
        execution = int(self.spec.get("trade_exemode", EXECUTION_MARKET))
        if filling == C["ORDER_FILLING_FOK"]:
            return bool(flags & SYMBOL_FILLING_FOK)
        if filling == C["ORDER_FILLING_IOC"]:
            return bool(flags & SYMBOL_FILLING_IOC)
        if filling == C["ORDER_FILLING_RETURN"]:
            # Verified: market execution symbols reject Return; exchange execution allows it.
            return execution != EXECUTION_MARKET
        return False

    def _stops_ok(self, buy: bool, reference: float, sl: float, tp: float) -> bool:
        """MetaTrader's rule: levels must be at least the stops level away from the price the
        position would close at (bid for a buy, ask for a sell) or the pending order's price."""
        distance = int(self.spec.get("trade_stops_level", 0)) * self.point - self.point / 2
        if buy:
            if sl and sl > reference - distance:
                return False
            if tp and tp < reference + distance:
                return False
        else:
            if sl and sl < reference + distance:
                return False
            if tp and tp > reference - distance:
                return False
        return True

    def _margin_ok(self, order_type: int, volume: float, price: float) -> bool:
        needed = self.calc_margin(order_type, volume, price)
        return self.equity - self.margin >= needed - 1e-9

    # --- requests ------------------------------------------------------------------------------

    def send(self, request: dict[str, Any], dry_run: bool = False) -> Outcome:
        """Carry out a request; with dry_run, only say what would happen (order_check)."""
        action = int(request.get("action", 0))
        if action == C["TRADE_ACTION_DEAL"]:
            return self._deal(request, dry_run)
        if action == C["TRADE_ACTION_PENDING"]:
            return self._place(request, dry_run)
        if action == C["TRADE_ACTION_SLTP"]:
            return self._modify_position(request, dry_run)
        if action == C["TRADE_ACTION_MODIFY"]:
            return self._modify_order(request, dry_run)
        if action == C["TRADE_ACTION_REMOVE"]:
            return self._remove_order(request, dry_run)
        if action == C["TRADE_ACTION_CLOSE_BY"]:
            raise SimulatorUnsupportedError("order_send: TRADE_ACTION_CLOSE_BY is not simulated.")
        return Outcome(C["TRADE_RETCODE_INVALID"], comment=f"Unknown trade action {action}")

    def _quote(self, retcode: int, **kwargs: Any) -> Outcome:
        return Outcome(retcode, bid=self.bid, ask=self.ask, **kwargs)

    def _deal(self, request: dict[str, Any], dry_run: bool) -> Outcome:
        if not self.has_price:
            return self._quote(C["TRADE_RETCODE_PRICE_OFF"])
        if not self.market_open:
            return self._quote(C["TRADE_RETCODE_MARKET_CLOSED"])
        order_type = int(request.get("type", -1))
        if order_type in STOP_LIMIT_TYPES:
            raise SimulatorUnsupportedError("order_send: stop-limit orders are not simulated.")
        if order_type not in (BUY, SELL):
            return self._quote(C["TRADE_RETCODE_INVALID"])
        volume = float(request.get("volume", 0.0))
        if not self._volume_ok(volume):
            return self._quote(C["TRADE_RETCODE_INVALID_VOLUME"])
        if not self._filling_ok(int(request.get("type_filling", C["ORDER_FILLING_FOK"]))):
            return self._quote(C["TRADE_RETCODE_INVALID_FILL"])
        buy = order_type == BUY
        price = self.ask if buy else self.bid
        position_ticket = int(request.get("position", 0) or 0)
        if position_ticket:
            position = self._position(position_ticket)
            if position is None:
                return self._quote(C["TRADE_RETCODE_POSITION_CLOSED"])
            if position.type == order_type or volume > position.volume + 1e-9:
                return self._quote(C["TRADE_RETCODE_INVALID"])
            if dry_run:
                return self._quote(DONE, volume=volume, price=price)
            order = self._filled_order(order_type, volume, price, request, position.ticket)
            deal = self._close(
                position,
                volume,
                price,
                order.ticket,
                C["DEAL_REASON_EXPERT"],
                str(request.get("comment", "")),
                magic=int(request.get("magic", 0)),
            )
            return self._quote(
                DONE, deal=deal.ticket, order=order.ticket, volume=volume, price=price
            )

        sl = float(request.get("sl", 0.0) or 0.0)
        tp = float(request.get("tp", 0.0) or 0.0)
        if not self._stops_ok(buy, self.bid if buy else self.ask, sl, tp):
            return self._quote(C["TRADE_RETCODE_INVALID_STOPS"])
        if self.account.margin_mode != HEDGING:
            return self._netting_deal(request, order_type, volume, price, sl, tp, dry_run)
        if not self._margin_ok(order_type, volume, price):
            return self._quote(C["TRADE_RETCODE_NO_MONEY"])
        if dry_run:
            return self._quote(DONE, volume=volume, price=price)
        order = self._filled_order(order_type, volume, price, request, 0)
        deal = self._open(
            order,
            order_type,
            volume,
            price,
            sl,
            tp,
            int(request.get("magic", 0)),
            str(request.get("comment", "")),
            C["DEAL_REASON_EXPERT"],
        )
        return self._quote(DONE, deal=deal.ticket, order=order.ticket, volume=volume, price=price)

    def _open(
        self,
        order: Order,
        order_type: int,
        volume: float,
        price: float,
        sl: float,
        tp: float,
        magic: int,
        comment: str,
        reason: int,
    ) -> DealRecord:
        position = Position(
            ticket=order.ticket,
            type=order_type,
            volume=volume,
            price_open=price,
            sl=sl,
            tp=tp,
            time_ms=self.price_time_ms,
            time_update_ms=self.price_time_ms,
            magic=magic,
            comment=comment,
            reason=C["POSITION_REASON_EXPERT"],
        )
        self.positions.append(position)
        return self._record_deal(
            order=order.ticket,
            deal_type=C["DEAL_TYPE_BUY"] if order_type == BUY else C["DEAL_TYPE_SELL"],
            entry=C["DEAL_ENTRY_IN"],
            position_id=position.ticket,
            volume=volume,
            price=price,
            profit=0.0,
            swap=0.0,
            magic=magic,
            reason=reason,
            comment=comment,
        )

    def _close(
        self,
        position: Position,
        volume: float,
        price: float,
        order: int,
        reason: int,
        comment: str,
        magic: int | None = None,
        entry: int | None = None,
    ) -> DealRecord:
        share = min(1.0, volume / position.volume) if position.volume else 1.0
        swap = position.swap * share
        profit = self.calc_profit(position.type, volume, position.price_open, price)
        deal = self._record_deal(
            order=order,
            deal_type=C["DEAL_TYPE_SELL"] if position.type == BUY else C["DEAL_TYPE_BUY"],
            entry=C["DEAL_ENTRY_OUT"] if entry is None else entry,
            position_id=position.ticket,
            volume=volume,
            price=price,
            profit=profit,
            swap=swap,
            magic=position.magic if magic is None else magic,
            reason=reason,
            comment=comment,
        )
        position.swap = _money(position.swap - swap)
        position.volume = round(position.volume - volume, 8)
        if position.volume <= 1e-9:
            self.positions.remove(position)
        return deal

    def _netting_deal(
        self,
        request: dict[str, Any],
        order_type: int,
        volume: float,
        price: float,
        sl: float,
        tp: float,
        dry_run: bool,
    ) -> Outcome:
        existing = next((p for p in self.positions if p.type in (BUY, SELL)), None)
        magic, comment = int(request.get("magic", 0)), str(request.get("comment", ""))
        if existing is None or existing.type == order_type:
            if not self._margin_ok(order_type, volume, price):
                return self._quote(C["TRADE_RETCODE_NO_MONEY"])
            if dry_run:
                return self._quote(DONE, volume=volume, price=price)
            if existing is None:
                order = self._filled_order(order_type, volume, price, request, 0)
                deal = self._open(
                    order,
                    order_type,
                    volume,
                    price,
                    sl,
                    tp,
                    magic,
                    comment,
                    C["DEAL_REASON_EXPERT"],
                )
            else:
                order = self._filled_order(order_type, volume, price, request, existing.ticket)
                total = existing.volume + volume
                existing.price_open = self._round_price(
                    (existing.price_open * existing.volume + price * volume) / total
                )
                existing.volume = round(total, 8)
                existing.time_update_ms = self.price_time_ms
                deal = self._record_deal(
                    order=order.ticket,
                    deal_type=C["DEAL_TYPE_BUY"] if order_type == BUY else C["DEAL_TYPE_SELL"],
                    entry=C["DEAL_ENTRY_IN"],
                    position_id=existing.ticket,
                    volume=volume,
                    price=price,
                    profit=0.0,
                    swap=0.0,
                    magic=magic,
                    reason=C["DEAL_REASON_EXPERT"],
                    comment=comment,
                )
            return self._quote(
                DONE, deal=deal.ticket, order=order.ticket, volume=volume, price=price
            )
        if dry_run:
            return self._quote(DONE, volume=volume, price=price)
        order = self._filled_order(order_type, volume, price, request, existing.ticket)
        if volume <= existing.volume + 1e-9:
            deal = self._close(
                existing, volume, price, order.ticket, C["DEAL_REASON_EXPERT"], comment, magic
            )
            return self._quote(
                DONE, deal=deal.ticket, order=order.ticket, volume=volume, price=price
            )
        remainder = round(volume - existing.volume, 8)
        closed = existing.volume
        profit = self.calc_profit(existing.type, closed, existing.price_open, price)
        swap = existing.swap
        self.positions.remove(existing)
        deal = self._record_deal(
            order=order.ticket,
            deal_type=C["DEAL_TYPE_BUY"] if order_type == BUY else C["DEAL_TYPE_SELL"],
            entry=C["DEAL_ENTRY_INOUT"],
            position_id=existing.ticket,
            volume=volume,
            price=price,
            profit=profit,
            swap=swap,
            magic=magic,
            reason=C["DEAL_REASON_EXPERT"],
            comment=comment,
        )
        self.positions.append(
            Position(
                ticket=existing.ticket,
                type=order_type,
                volume=remainder,
                price_open=price,
                sl=sl,
                tp=tp,
                time_ms=self.price_time_ms,
                time_update_ms=self.price_time_ms,
                magic=magic,
                comment=comment,
                reason=C["POSITION_REASON_EXPERT"],
            )
        )
        return self._quote(DONE, deal=deal.ticket, order=order.ticket, volume=volume, price=price)

    def _position(self, ticket: int) -> Position | None:
        return next((p for p in self.positions if p.ticket == ticket), None)

    def _order(self, ticket: int) -> Order | None:
        return next((o for o in self.orders if o.ticket == ticket), None)

    def _expiration_ms(self, request: dict[str, Any], type_time: int) -> int | None:
        """Expiry of a pending order in server ms, 0 for none, None when invalid."""
        if type_time == C["ORDER_TIME_GTC"]:
            return 0
        day_end = (self.price_time_ms // DAY_MS + 1) * DAY_MS
        if type_time == C["ORDER_TIME_DAY"]:
            return day_end
        raw = request.get("expiration", 0)
        if isinstance(raw, datetime):
            moment = raw if raw.tzinfo else raw.replace(tzinfo=UTC)
            expiry = int(moment.timestamp() * 1000)
        else:
            expiry = int(raw or 0) * 1000
        if expiry <= self.price_time_ms:
            return None
        if type_time == C["ORDER_TIME_SPECIFIED_DAY"]:
            expiry = (expiry // DAY_MS + 1) * DAY_MS
        return expiry

    def _pending_price_ok(self, order_type: int, price: float) -> bool:
        distance = int(self.spec.get("trade_stops_level", 0)) * self.point - self.point / 2
        if order_type == BUY_LIMIT:
            return price <= self.ask - distance
        if order_type == SELL_LIMIT:
            return price >= self.bid + distance
        if order_type == BUY_STOP:
            return price >= self.ask + distance
        return price <= self.bid - distance

    def _place(self, request: dict[str, Any], dry_run: bool) -> Outcome:
        if not self.has_price:
            return self._quote(C["TRADE_RETCODE_PRICE_OFF"])
        if not self.market_open:
            return self._quote(C["TRADE_RETCODE_MARKET_CLOSED"])
        order_type = int(request.get("type", -1))
        if order_type in STOP_LIMIT_TYPES:
            raise SimulatorUnsupportedError("order_send: stop-limit orders are not simulated.")
        if order_type not in PENDING_TYPES:
            return self._quote(C["TRADE_RETCODE_INVALID"])
        volume = float(request.get("volume", 0.0))
        if not self._volume_ok(volume):
            return self._quote(C["TRADE_RETCODE_INVALID_VOLUME"])
        price = float(request.get("price", 0.0) or 0.0)
        if price <= 0 or not self._pending_price_ok(order_type, price):
            return self._quote(C["TRADE_RETCODE_INVALID_PRICE"])
        sl = float(request.get("sl", 0.0) or 0.0)
        tp = float(request.get("tp", 0.0) or 0.0)
        if not self._stops_ok(order_type in (BUY_LIMIT, BUY_STOP), price, sl, tp):
            return self._quote(C["TRADE_RETCODE_INVALID_STOPS"])
        type_time = int(request.get("type_time", C["ORDER_TIME_GTC"]))
        expiry = self._expiration_ms(request, type_time)
        if expiry is None:
            return self._quote(C["TRADE_RETCODE_INVALID_EXPIRATION"])
        if dry_run:
            return self._quote(PLACED, volume=volume, price=price)
        ticket = self._new_order_ticket()
        self.orders.append(
            Order(
                ticket=ticket,
                type=order_type,
                volume_initial=volume,
                volume_current=volume,
                price_open=price,
                sl=sl,
                tp=tp,
                time_setup_ms=self.price_time_ms,
                type_time=type_time,
                type_filling=int(request.get("type_filling", C["ORDER_FILLING_RETURN"])),
                time_expiration_ms=expiry,
                magic=int(request.get("magic", 0)),
                comment=str(request.get("comment", "")),
                reason=C["ORDER_REASON_EXPERT"],
            )
        )
        return self._quote(PLACED, order=ticket, volume=volume, price=price)

    def _modify_position(self, request: dict[str, Any], dry_run: bool) -> Outcome:
        position = self._position(int(request.get("position", 0) or 0))
        if position is None:
            return self._quote(C["TRADE_RETCODE_POSITION_CLOSED"])
        if not self.market_open:
            return self._quote(C["TRADE_RETCODE_MARKET_CLOSED"])
        sl = float(request.get("sl", 0.0) or 0.0)
        tp = float(request.get("tp", 0.0) or 0.0)
        buy = position.type == BUY
        if not self._stops_ok(buy, self.bid if buy else self.ask, sl, tp):
            return self._quote(C["TRADE_RETCODE_INVALID_STOPS"])
        if math.isclose(sl, position.sl) and math.isclose(tp, position.tp):
            return self._quote(C["TRADE_RETCODE_NO_CHANGES"])
        if dry_run:
            return self._quote(DONE)
        position.sl, position.tp = sl, tp
        position.time_update_ms = self.price_time_ms
        return self._quote(DONE)

    def _modify_order(self, request: dict[str, Any], dry_run: bool) -> Outcome:
        order = self._order(int(request.get("order", 0) or 0))
        if order is None:
            return self._quote(C["TRADE_RETCODE_INVALID_ORDER"])
        if not self.market_open:
            return self._quote(C["TRADE_RETCODE_MARKET_CLOSED"])
        price = float(request.get("price", order.price_open) or order.price_open)
        if not self._pending_price_ok(order.type, price):
            return self._quote(C["TRADE_RETCODE_INVALID_PRICE"])
        sl = float(request.get("sl", 0.0) or 0.0)
        tp = float(request.get("tp", 0.0) or 0.0)
        if not self._stops_ok(order.type in (BUY_LIMIT, BUY_STOP), price, sl, tp):
            return self._quote(C["TRADE_RETCODE_INVALID_STOPS"])
        type_time = int(request.get("type_time", order.type_time))
        expiry = self._expiration_ms(request, type_time)
        if expiry is None:
            return self._quote(C["TRADE_RETCODE_INVALID_EXPIRATION"])
        if dry_run:
            return self._quote(DONE, order=order.ticket)
        order.price_open, order.sl, order.tp = price, sl, tp
        order.type_time, order.time_expiration_ms = type_time, expiry
        return self._quote(DONE, order=order.ticket)

    def _remove_order(self, request: dict[str, Any], dry_run: bool) -> Outcome:
        order = self._order(int(request.get("order", 0) or 0))
        if order is None:
            return self._quote(C["TRADE_RETCODE_INVALID_ORDER"])
        if not self.market_open:
            return self._quote(C["TRADE_RETCODE_MARKET_CLOSED"])
        if dry_run:
            return self._quote(DONE, order=order.ticket)
        self._retire(order, C["ORDER_STATE_CANCELED"])
        return self._quote(DONE, order=order.ticket)

    def _retire(self, order: Order, state: int) -> None:
        order.state = state
        order.time_done_ms = self.price_time_ms
        self.orders.remove(order)
        self.history_orders.append(order)

    def check(self, request: dict[str, Any]) -> tuple[int, float]:
        """What order_send would answer, without doing it: (retcode, margin needed)."""
        outcome = self.send(request, dry_run=True)
        volume = float(request.get("volume", 0.0) or 0.0)
        order_type = int(request.get("type", 0))
        price = self.ask if order_type == BUY else self.bid
        retcode = 0 if outcome.retcode in (DONE, PLACED) else outcome.retcode
        return retcode, self.calc_margin(order_type, volume, price)

    # --- moving through time -------------------------------------------------------------------

    def _trigger(self, lo: int, hi: int) -> tuple[int, list[tuple[str, Any]]]:
        """Earliest index in [lo, hi) where something happens, and what happens there."""
        stream = self.market.stream
        best = hi
        events: list[tuple[str, Any]] = []

        def offer(index: int, event: tuple[str, Any]) -> None:
            nonlocal best, events
            if index < best:
                best, events = index, [event]
            elif index == best:
                events.append(event)

        bids, asks = stream.bid[lo:hi], stream.ask[lo:hi]
        tradable = self._tradable[lo:hi]
        days = stream.time_ms[lo:hi] // DAY_MS
        new_day = np.flatnonzero(days != self._day)
        if len(new_day):
            offer(lo + int(new_day[0]), ("rollover", None))
        for order in self.orders:
            if order.type == BUY_LIMIT:
                hits = self._at_or_below(asks, order.price_open)
            elif order.type == SELL_LIMIT:
                hits = self._at_or_above(bids, order.price_open)
            elif order.type == BUY_STOP:
                hits = self._at_or_above(asks, order.price_open)
            else:
                hits = self._at_or_below(bids, order.price_open)
            found = np.flatnonzero(hits & tradable)
            if len(found):
                offer(lo + int(found[0]), ("fill", order))
            if order.time_expiration_ms:
                expired = int(np.searchsorted(stream.time_ms[lo:hi], order.time_expiration_ms))
                if expired < hi - lo:
                    offer(lo + expired, ("expire", order))
        for position in self.positions:
            prices = bids if position.type == BUY else asks
            hits = np.zeros(len(prices), bool)
            if position.type == BUY:
                if position.sl:
                    hits = self._at_or_below(prices, position.sl)
                if position.tp:
                    hits = hits | self._at_or_above(prices, position.tp)
            else:
                if position.sl:
                    hits = self._at_or_above(prices, position.sl)
                if position.tp:
                    hits = hits | self._at_or_below(prices, position.tp)
            found = np.flatnonzero(hits & tradable)
            if len(found):
                offer(lo + int(found[0]), ("stop", position))
        return best, events

    def advance(self, target_ms: int, end_ms: int) -> None:
        """Handle everything that happens up to `target_ms` (never at or after `end_ms`)."""
        stream = self.market.stream
        self.now_ms = min(target_ms, end_ms)
        limit = min(
            int(np.searchsorted(stream.time_ms, target_ms, side="right")),
            int(np.searchsorted(stream.time_ms, end_ms, side="left")),
        )
        while self.index + 1 < limit:
            if not (self.positions or self.orders):
                self._day_from(limit - 1)
                self.index = limit - 1
                break
            index, events = self._trigger(self.index + 1, limit)
            if index >= limit:
                self._day_from(limit - 1)
                self.index = limit - 1
                break
            if any(kind == "rollover" for kind, _ in events):
                self._rollover(int(stream.time_ms[index]) // DAY_MS, index - 1)
            self.index = index
            for kind, subject in events:
                if kind == "fill" and subject in self.orders:
                    self._fill_pending(subject)
                elif kind == "expire" and subject in self.orders:
                    self._retire(subject, C["ORDER_STATE_EXPIRED"])
            for kind, subject in events:
                if kind == "stop" and subject in self.positions:
                    self._stop_out(subject)

    def _day_from(self, index: int) -> None:
        if index >= 0:
            day = int(self.market.stream.time_ms[index]) // DAY_MS
            if day != self._day:
                self._rollover(day, self.index)

    def _rollover(self, new_day: int, price_index: int) -> None:
        """Charge swap for every midnight between the last price's day and `new_day`, converted
        at the price of `price_index`, the last one before midnight."""
        rollover3 = MQL_TO_PY_WEEKDAY.get(int(self.spec.get("swap_rollover3days", 3)), 2)
        for day in range(self._day, new_day):
            weekday = (datetime(1970, 1, 1) + timedelta(days=day)).weekday()
            if weekday >= 5:
                continue
            nights = 3 if weekday == rollover3 else 1
            for position in self.positions:
                charge = self._swap(position, price_index) * nights
                position.swap = _money(position.swap + charge)
        self._day = new_day

    def _swap(self, position: Position, price_index: int) -> float:
        s = self.spec
        mode = int(s.get("swap_mode", 0))
        rate = float(s["swap_long"] if position.type == BUY else s["swap_short"])
        stream = self.market.stream
        index = max(0, price_index)
        price = float(stream.bid[index] if position.type == BUY else stream.ask[index])
        if mode == C["SYMBOL_SWAP_MODE_POINTS"]:
            return self._to_account(rate * self.point * self.contract * position.volume, price)
        if mode == C["SYMBOL_SWAP_MODE_CURRENCY_DEPOSIT"]:
            return rate * position.volume
        if mode in (C["SYMBOL_SWAP_MODE_CURRENCY_SYMBOL"], C["SYMBOL_SWAP_MODE_CURRENCY_MARGIN"]):
            if s.get("currency_base", "").upper() == self.account.currency.upper():
                return rate * position.volume
            return rate * position.volume * price
        return 0.0

    def _fill_pending(self, order: Order) -> None:
        stream = self.market.stream
        buy = order.type in (BUY_LIMIT, BUY_STOP)
        market = self.ask if buy else self.bid
        stop_order = order.type in (BUY_STOP, SELL_STOP)
        on_ticks = not stream.synthetic
        price = self._round_price(market if stop_order and on_ticks else order.price_open)
        order_type = BUY if buy else SELL
        if not self._margin_ok(order_type, order.volume_current, price):
            self._retire(order, C["ORDER_STATE_REJECTED"])
            return
        self.orders.remove(order)
        order.state = C["ORDER_STATE_FILLED"]
        order.time_done_ms = self.price_time_ms
        order.position_id = order.ticket
        self.history_orders.append(order)
        self._open(
            order,
            order_type,
            order.volume_current,
            price,
            order.sl,
            order.tp,
            order.magic,
            order.comment,
            C["DEAL_REASON_EXPERT"],
        )
        order.volume_current = 0.0

    def _stop_out(self, position: Position) -> None:
        stream = self.market.stream
        buy = position.type == BUY
        price_now = self.bid if buy else self.ask
        if buy:
            hit_sl = bool(position.sl) and bool(self._at_or_below(price_now, position.sl))
        else:
            hit_sl = bool(position.sl) and bool(self._at_or_above(price_now, position.sl))
        level = position.sl if hit_sl else position.tp
        price = self._round_price(level if stream.synthetic else price_now)
        kind = "sl" if hit_sl else "tp"
        reason = C["DEAL_REASON_SL"] if hit_sl else C["DEAL_REASON_TP"]
        order = self._filled_order(
            SELL if buy else BUY, position.volume, price, {"magic": position.magic}, position.ticket
        )
        order.reason = C["ORDER_REASON_SL"] if hit_sl else C["ORDER_REASON_TP"]
        self._close(
            position,
            position.volume,
            price,
            order.ticket,
            reason,
            f"{kind} {level:.{self.digits}f}",
        )

    def finish(self) -> None:
        """End of the run: close every position at the last price and cancel pending orders."""
        for order in list(self.orders):
            self._retire(order, C["ORDER_STATE_CANCELED"])
        for position in list(self.positions):
            buy = position.type == BUY
            price = self.bid if buy else self.ask
            order = self._filled_order(
                SELL if buy else BUY,
                position.volume,
                price,
                {"magic": position.magic},
                position.ticket,
            )
            self._close(
                position,
                position.volume,
                price,
                order.ticket,
                C["DEAL_REASON_EXPERT"],
                "end of test",
            )

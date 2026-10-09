"""The normalised backtest result that both engines produce and the frontend consumes.

Times are carried twice: `server_time` exactly as the broker's server clock shows it (which is
what MetaTrader displays everywhere), and `time` in UTC. The UTC value is None when the server's
offset for that moment is unknown; it is never filled in by assumption.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, Field


class Engine(StrEnum):
    MT5_TESTER = "mt5_tester"
    PYTHON_SIM = "python_sim"


class TickModel(StrEnum):
    EVERY_TICK = "every_tick"
    OHLC_M1 = "ohlc_m1"
    OPEN_PRICES = "open_prices"
    REAL_TICKS = "real_ticks"


class Deal(BaseModel):
    ticket: int
    server_time: datetime
    time: AwareDatetime | None
    symbol: str | None
    type: str = Field(description="buy, sell, balance, credit, ... as MetaTrader names them")
    entry: str | None = Field(description="in, out, in/out or out by; None for balance deals")
    volume: float | None
    price: float | None
    order: int | None
    commission: float
    swap: float
    profit: float
    balance: float | None = Field(description="Balance after the deal, as the engine reported it")
    comment: str = ""

    @property
    def is_trade(self) -> bool:
        return self.type in ("buy", "sell")

    @property
    def closes_position(self) -> bool:
        return self.is_trade and self.entry in ("out", "in/out", "out by")


class Order(BaseModel):
    ticket: int
    open_server_time: datetime
    open_time: AwareDatetime | None
    symbol: str | None
    type: str
    volume_initial: float | None
    volume_filled: float | None
    price: float | None
    sl: float | None
    tp: float | None
    done_server_time: datetime | None
    done_time: AwareDatetime | None
    state: str
    comment: str = ""


class BalancePoint(BaseModel):
    server_time: datetime
    time: AwareDatetime | None
    balance: float
    equity: float | None = None


class RunMeta(BaseModel):
    engine: Engine
    fidelity: str
    strategy_name: str
    strategy_hash: str | None
    symbol: str
    timeframe: str
    date_from: date
    date_to: date = Field(description="Exclusive: the run stops at 00:00 server time on this date")
    model: TickModel
    deposit: float
    currency: str
    leverage: int
    parameters: dict[str, str]
    server: str | None = None
    company: str | None = None
    terminal_build: int | None = None
    server_utc_offsets_h: list[int] = Field(
        default_factory=list,
        description="Distinct server UTC offsets (hours) in effect during the run",
    )
    notes: list[str] = Field(default_factory=list)


class BacktestResult(BaseModel):
    meta: RunMeta
    deals: list[Deal]
    orders: list[Order] = Field(default_factory=list)
    balance: list[BalancePoint]
    metrics: dict[str, float | int | None] = Field(default_factory=dict)
    reported: dict[str, str] = Field(
        default_factory=dict,
        description="Summary figures exactly as the engine printed them (Strategy Tester only)",
    )

    @property
    def trade_count(self) -> int:
        return sum(1 for deal in self.deals if deal.closes_position)

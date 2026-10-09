"""Hand-built price data for simulator tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from strategylab.sim.broker import AccountSettings, Broker
from strategylab.sim.clock import SimClock
from strategylab.sim.constants import CONSTANTS, RECORD_FIELDS
from strategylab.sim.market import RATE_DTYPE, Market, synthetic_stream
from strategylab.sim.shim import TerminalStandIn

POINT = 0.00001
DIGITS = 5


def ms(*args: int) -> int:
    return int(datetime(*args, tzinfo=UTC).timestamp() * 1000)


def seconds(*args: int) -> int:
    return ms(*args) // 1000


def spec(**changes):
    """A EURUSD-like symbol on a hedging account, as exported from a terminal."""
    values = dict.fromkeys(RECORD_FIELDS["SymbolInfo"], 0)
    values.update(
        name="EURUSD@",
        digits=DIGITS,
        point=POINT,
        trade_tick_size=POINT,
        trade_tick_value=1.0,
        trade_contract_size=100_000.0,
        volume_min=0.01,
        volume_step=0.01,
        volume_max=50.0,
        trade_stops_level=10,
        filling_mode=1,
        trade_exemode=CONSTANTS["SYMBOL_TRADE_EXECUTION_MARKET"],
        trade_calc_mode=CONSTANTS["SYMBOL_CALC_MODE_FOREX"],
        swap_mode=CONSTANTS["SYMBOL_SWAP_MODE_POINTS"],
        swap_long=-7.6,
        swap_short=1.6,
        swap_rollover3days=3,
        currency_base="EUR",
        currency_profit="USD",
        currency_margin="EUR",
        description="Euro vs US Dollar",
        path="Forex\\EURUSD@",
        bid=9.99,
        ask=9.99,
        time=2_000_000_000,
        session_open=9.99,
        price_change=99.0,
    )
    values.update(changes)
    return values


def bars(
    start: datetime,
    rows: list[tuple[float, float, float, float]],
    *,
    volume=10,
    spread=10,
    step_minutes=1,
):
    """M1 bars from (open, high, low, close) rows, one minute apart unless told otherwise."""
    out = np.zeros(len(rows), dtype=RATE_DTYPE)
    base = int(start.replace(tzinfo=UTC).timestamp())
    for i, (o, h, l, c) in enumerate(rows):  # noqa: E741
        out[i] = (base + 60 * step_minutes * i, o, h, l, c, volume, spread, 0)
    return out


def flat_bars(start: datetime, count: int, price: float = 1.1, *, step_minutes=1, spread=10):
    return bars(
        start, [(price, price, price, price)] * count, step_minutes=step_minutes, spread=spread
    )


def simulation(
    m1, start_ms, end_ms, *, deposit=10_000.0, margin_mode=None, symbol_spec=None, native=None
):
    """Market, broker, clock and terminal over hand-built bars."""
    symbol_spec = symbol_spec or spec()
    stream = synthetic_stream(m1, POINT, DIGITS)
    market = Market(symbol_spec["name"], m1, stream, native or {})
    account = AccountSettings(
        deposit=deposit,
        currency="USD",
        leverage=100,
        margin_mode=CONSTANTS["ACCOUNT_MARGIN_MODE_RETAIL_HEDGING"]
        if margin_mode is None
        else margin_mode,
    )
    broker = Broker(market, symbol_spec, account, start_ms)
    clock = SimClock(
        event_times_ms=stream.time_ms,
        now_ms=start_ms,
        end_ms=end_ms,
        advance=lambda target: broker.advance(target, end_ms),
        finish=broker.finish,
    )
    terminal = TerminalStandIn(broker, clock, market, 6249)
    return market, broker, clock, terminal


def write_dataset(folder: Path, m1, symbol_spec=None) -> tuple[Path, Path]:
    """Parquet bars and a spec file laid out as mt5_data exports them."""
    folder.mkdir(parents=True, exist_ok=True)
    m1_path = folder / "m1.parquet"
    pd.DataFrame(m1).to_parquet(m1_path, index=False)
    spec_path = folder / "spec.json"
    spec_path.write_text(
        json.dumps(
            {
                "symbol": symbol_spec or spec(),
                "account_currency": "USD",
                "margin_mode": CONSTANTS["ACCOUNT_MARGIN_MODE_RETAIL_HEDGING"],
                "server": "Test-Server",
                "terminal_build": 6249,
            }
        ),
        encoding="utf-8",
    )
    return m1_path, spec_path

"""Strategy Tester report parsing into the normalised result.

The tester writes an HTML 4 report in UTF-16LE with two tables. The first holds the Settings and
Results sections as label/value cells; the second holds the Orders and Deals tables, each under a
one-cell heading row. Settings and the two trade tables are read by position, so a report from a
terminal running in another language parses the same way. Summary figures are keyed by their
English labels; other languages keep their own labels as keys. See DECISIONS.md.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from bs4 import BeautifulSoup, Tag

from strategylab.config import decode_mt_text
from strategylab.result import (
    BacktestResult,
    BalancePoint,
    Deal,
    Engine,
    Order,
    RunMeta,
    TickModel,
)
from strategylab.server_clock import ServerClock

DEAL_COLUMNS = 13
ORDER_COLUMNS = 11
MONEY_TOLERANCE = 0.005

_SPACES = re.compile(r"[\s\u00a0\u202f']")
_NUMBER = re.compile(r"[-+]?[\d\s\u00a0\u202f',.]*\d")
_PERIOD = re.compile(
    r"^(?P<tf>\S+)\s*\((?P<start>\d{4}\.\d{2}\.\d{2})\s*-\s*(?P<end>\d{4}\.\d{2}\.\d{2})\)"
)
_SERVER = re.compile(r"^(?P<server>.*?)\s*\(\D*(?P<build>\d+)\)\s*$")


class ReportError(Exception):
    pass


def parse_number(text: str) -> float | None:
    """Read the first number in a report cell, whatever the grouping style.

    Reports group thousands with spaces ("10 000.00"); terminals in other locales may use
    no-break spaces, apostrophes, or commas. When both '.' and ',' appear the later one is the
    decimal separator; a lone ',' is decimal unless exactly three digits follow it.
    """
    match = _NUMBER.search(text or "")
    if not match:
        return None
    raw = _SPACES.sub("", match.group(0))
    if "," in raw and "." in raw:
        decimal = "." if raw.rfind(".") > raw.rfind(",") else ","
        group = "," if decimal == "." else "."
        raw = raw.replace(group, "").replace(decimal, ".")
    elif "," in raw:
        head, _, tail = raw.rpartition(",")
        if len(tail) == 3 and head.lstrip("+-"):
            raw = raw.replace(",", "")
        else:
            raw = head.replace(",", "") + "." + tail
    elif raw.count(".") > 1:
        raw = raw.replace(".", "")
    try:
        return float(raw)
    except ValueError:
        return None


def parse_int(text: str) -> int | None:
    value = parse_number(text)
    return int(value) if value is not None else None


def parse_server_time(text: str) -> datetime | None:
    text = (text or "").strip()
    for pattern in ("%Y.%m.%d %H:%M:%S", "%Y.%m.%d %H:%M", "%Y.%m.%d"):
        try:
            return datetime.strptime(text, pattern)
        except ValueError:
            continue
    return None


def _parse_date(text: str) -> date:
    return datetime.strptime(text, "%Y.%m.%d").date()


@dataclass(frozen=True)
class ReportSettings:
    expert: str | None = None
    symbol: str | None = None
    timeframe: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    inputs: dict[str, str] = field(default_factory=dict)
    company: str | None = None
    currency: str | None = None
    deposit: float | None = None
    leverage: int | None = None
    server: str | None = None
    build: int | None = None


@dataclass(frozen=True)
class ParsedReport:
    settings: ReportSettings
    summary: dict[str, str]
    orders: list[Order]
    deals: list[Deal]
    notes: tuple[str, ...] = ()

    @property
    def trade_count(self) -> int:
        return sum(1 for deal in self.deals if deal.closes_position)


def read_report_text(path: Path) -> str:
    """Decode a report file; the tester writes UTF-16LE with a BOM, anything else is tolerated."""
    return decode_mt_text(path.read_bytes())


def _cells(row: Tag) -> list[Tag]:
    return row.find_all(["td", "th"], recursive=False)


def _text(cell: Tag) -> str:
    return cell.get_text(" ", strip=True)


def _is_heading(row: Tag) -> bool:
    cells = [c for c in _cells(row) if _text(c)]
    return len(cells) == 1 and cells[0].get("colspan") == "13"


def _settings_and_results(table: Tag) -> tuple[ReportSettings, dict[str, str]]:
    rows = table.find_all("tr")
    headings = [i for i, row in enumerate(rows) if _is_heading(row)]
    # Headings: title, "server (Build n)", Settings, Results (in that order).
    if len(headings) < 4:
        raise ReportError("The report has no Settings and Results sections.")
    server_line = _text(rows[headings[1]])
    server = build = None
    match = _SERVER.match(server_line)
    if match:
        server, build = match["server"], int(match["build"])

    setting_rows = rows[headings[2] + 1 : headings[3]]
    pairs: list[tuple[str, str]] = []
    for row in setting_rows:
        texts = [_text(c) for c in _cells(row)]
        if not any(texts):
            continue
        label = texts[0] if len(texts) > 1 else ""
        value = texts[-1]
        pairs.append((label, value))

    # Fixed order: expert, symbol, period, inputs (one row each, only the first labelled),
    # then company, currency, initial deposit, leverage.
    if len(pairs) < 3:
        raise ReportError("The report's Settings section is incomplete.")
    expert, symbol, period = (value for _, value in pairs[:3])
    rest = pairs[3:]
    inputs: dict[str, str] = {}
    tail_start = 0
    for index, (label, value) in enumerate(rest):
        if "=" in value and (index == 0 or not label):
            name, _, input_value = value.partition("=")
            inputs[name.strip()] = input_value.strip()
            tail_start = index + 1
        else:
            break
    tail = [value for _, value in rest[tail_start:]]
    company, currency, deposit, leverage = (tail + [None] * 4)[:4]

    timeframe = date_from = date_to = None
    period_match = _PERIOD.match(period)
    if period_match:
        timeframe = period_match["tf"]
        date_from = _parse_date(period_match["start"])
        date_to = _parse_date(period_match["end"])

    leverage_value = None
    if leverage and ":" in leverage:
        leverage_value = parse_int(leverage.split(":", 1)[1])

    settings = ReportSettings(
        expert=expert,
        symbol=symbol,
        timeframe=timeframe,
        date_from=date_from,
        date_to=date_to,
        inputs=inputs,
        company=company,
        currency=currency,
        deposit=parse_number(deposit) if deposit else None,
        leverage=leverage_value,
        server=server,
        build=build,
    )

    summary: dict[str, str] = {}
    for row in rows[headings[3] + 1 :]:
        texts = [_text(c) for c in _cells(row)]
        for label, value in zip(texts[::2], texts[1::2], strict=False):
            if label.endswith(":"):
                summary[label[:-1].strip()] = value
    return settings, summary


def _data_rows(rows: list[Tag], width: int) -> list[list[str]]:
    """Rows of exactly `width` cells after the column-title row, skipping the totals row."""
    data = []
    for row in rows[1:]:
        cells = _cells(row)
        if len(cells) != width:
            continue
        data.append([_text(c) for c in cells])
    return data


def _float_or_none(text: str) -> float | None:
    return parse_number(text) if text.strip() else None


def _orders(rows: list[Tag]) -> list[Order]:
    orders = []
    for cells in _data_rows(rows, ORDER_COLUMNS):
        opened = parse_server_time(cells[0])
        if opened is None:
            continue
        initial, _, filled = cells[4].partition("/")
        orders.append(
            Order(
                ticket=int(cells[1]),
                open_server_time=opened,
                open_time=None,
                symbol=cells[2] or None,
                type=cells[3],
                volume_initial=_float_or_none(initial),
                volume_filled=_float_or_none(filled),
                price=_float_or_none(cells[5]),
                sl=_float_or_none(cells[6]),
                tp=_float_or_none(cells[7]),
                done_server_time=parse_server_time(cells[8]),
                done_time=None,
                state=cells[9],
                comment=cells[10],
            )
        )
    return orders


def _deals(rows: list[Tag]) -> list[Deal]:
    deals = []
    for cells in _data_rows(rows, DEAL_COLUMNS):
        when = parse_server_time(cells[0])
        if when is None:
            continue
        deals.append(
            Deal(
                ticket=int(cells[1]),
                server_time=when,
                time=None,
                symbol=cells[2] or None,
                type=cells[3],
                entry=cells[4] or None,
                volume=_float_or_none(cells[5]),
                price=_float_or_none(cells[6]),
                order=parse_int(cells[7]) if cells[7] else None,
                commission=parse_number(cells[8]) or 0.0,
                swap=parse_number(cells[9]) or 0.0,
                profit=parse_number(cells[10]) or 0.0,
                balance=_float_or_none(cells[11]),
                comment=cells[12],
            )
        )
    return deals


def parse_report_html(html: str) -> ParsedReport:
    soup = BeautifulSoup(html, "lxml")
    tables = soup.find_all("table")
    if not tables:
        raise ReportError("The file contains no tables; it is not a Strategy Tester report.")
    settings, summary = _settings_and_results(tables[0])

    orders: list[Order] = []
    deals: list[Deal] = []
    if len(tables) > 1:
        rows = tables[1].find_all("tr")
        heads = [i for i, row in enumerate(rows) if row.find("th") and _text(row)]
        # Orders come first, then Deals; each heading is followed by a column-title row.
        if len(heads) >= 2:
            orders = _orders(rows[heads[0] + 1 : heads[1]])
            deals = _deals(rows[heads[1] + 1 :])
        elif len(heads) == 1:
            deals = _deals(rows[heads[0] + 1 :])
    if not deals:
        raise ReportError("The report has no Deals table.")
    return ParsedReport(settings, summary, orders, deals)


def parse_report(path: Path) -> ParsedReport:
    return parse_report_html(read_report_text(path))


def rebuild_balance(deals: list[Deal]) -> tuple[list[BalancePoint], list[str]]:
    """Balance after every deal, summed from the deals' own money columns.

    The tester prints a balance column as well; it is used only as a check. A disagreement is
    reported rather than corrected, because it would mean a deal was misread.
    """
    points: list[BalancePoint] = []
    notes: list[str] = []
    balance = 0.0
    for deal in deals:
        balance += deal.profit + deal.commission + deal.swap
        balance = round(balance, 2)
        if deal.balance is not None and abs(deal.balance - balance) > MONEY_TOLERANCE and not notes:
            notes.append(
                f"Balance rebuilt from deals ({balance:.2f}) differs from the report's balance "
                f"({deal.balance:.2f}) at deal {deal.ticket}."
            )
        points.append(BalancePoint(server_time=deal.server_time, time=deal.time, balance=balance))
    return points, notes


def apply_clock(parsed: ParsedReport, clock: ServerClock | None) -> tuple[list[Deal], list[Order]]:
    if clock is None:
        return parsed.deals, parsed.orders
    deals = [d.model_copy(update={"time": clock.to_utc(d.server_time)}) for d in parsed.deals]
    orders = [
        o.model_copy(
            update={
                "open_time": clock.to_utc(o.open_server_time),
                "done_time": clock.to_utc(o.done_server_time) if o.done_server_time else None,
            }
        )
        for o in parsed.orders
    ]
    return deals, orders


def to_result(
    parsed: ParsedReport,
    *,
    model: TickModel,
    fidelity: str,
    strategy_hash: str | None,
    clock: ServerClock | None,
    expected: Mapping[str, object] | None = None,
) -> BacktestResult:
    """Build the normalised result. Settings come from the report itself, which records what the
    tester actually used; `expected` fills anything the report omits and flags disagreements."""
    s = parsed.settings
    notes = list(parsed.notes)
    deals, orders = apply_clock(parsed, clock)
    balance, balance_notes = rebuild_balance(deals)
    notes += balance_notes

    def pick(name: str, reported: object) -> object:
        wanted = (expected or {}).get(name)
        if reported is None:
            return wanted
        if wanted is not None and reported != wanted:
            notes.append(f"Requested {name} {wanted!r} but the report shows {reported!r}.")
        return reported

    first = deals[0].server_time
    last = deals[-1].server_time
    offsets: list[int] = []
    if clock is None:
        notes.append("Server UTC offset unknown: deal times are broker server time only.")
    else:
        offsets = clock.offsets_between(first, last)
        if any(d.time is None for d in deals):
            notes.append("Some deals predate the measured server clock; their UTC time is unknown.")

    meta = RunMeta(
        engine=Engine.MT5_TESTER,
        fidelity=fidelity,
        strategy_name=str(s.expert or (expected or {}).get("strategy_name") or ""),
        strategy_hash=strategy_hash,
        symbol=str(pick("symbol", s.symbol) or ""),
        timeframe=str(pick("timeframe", s.timeframe) or ""),
        date_from=pick("date_from", s.date_from),
        date_to=pick("date_to", s.date_to),
        model=model,
        deposit=float(pick("deposit", s.deposit) or 0.0),
        currency=str(pick("currency", s.currency) or ""),
        leverage=int(pick("leverage", s.leverage) or 0),
        parameters=dict(s.inputs),
        server=s.server,
        company=s.company,
        terminal_build=s.build,
        server_utc_offsets_h=offsets,
        notes=notes,
    )
    return BacktestResult(
        meta=meta,
        deals=deals,
        orders=orders,
        balance=balance,
        reported=dict(parsed.summary),
    )

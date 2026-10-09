import codecs
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from strategylab.report import (
    ReportError,
    parse_number,
    parse_report,
    parse_report_html,
    read_report_text,
    rebuild_balance,
    to_result,
)
from strategylab.result import Engine, TickModel
from strategylab.server_clock import ServerClock, WeekOffset

FIXTURES = Path(__file__).parent / "fixtures" / "reports"
PROFITABLE = FIXTURES / "ma_eurusd_h1_2025.htm"
NO_TRADES = FIXTURES / "ma_no_trades.htm"


@pytest.fixture(scope="module")
def profitable():
    return parse_report(PROFITABLE)


@pytest.fixture(scope="module")
def no_trades():
    return parse_report(NO_TRADES)


def fixed_clock(offset_winter=2, offset_summer=3):
    weeks = [
        WeekOffset(datetime(2024, 12, 30), offset_winter),
        WeekOffset(datetime(2025, 3, 10), offset_summer),
        WeekOffset(datetime(2025, 11, 3), offset_winter),
    ]
    return ServerClock("WMMarkets-Demo", "EURUSD@", tuple(weeks), datetime.now(UTC))


class TestNumbers:
    @pytest.mark.parametrize(
        ("text", "value"),
        [
            ("10 000.00", 10000.0),
            ("-3 482.02", -3482.02),
            ("684.68 (5.87%)", 684.68),
            ("5.87% (684.68)", 5.87),
            ("1.0004 (0.04%)", 1.0004),
            ("10\u00a0000,50", 10000.5),
            ("1\u202f234\u202f567.25", 1234567.25),
            ("1.234,5", 1234.5),
            ("1,234.5", 1234.5),
            ("12,5", 12.5),
            ("1'000.5", 1000.5),
            ("0.00000", 0.0),
            ("", None),
            ("n/a", None),
        ],
    )
    def test_parse_number(self, text, value):
        assert parse_number(text) == value


class TestProfitableReport:
    def test_file_is_utf16_with_bom(self):
        assert PROFITABLE.read_bytes().startswith(codecs.BOM_UTF16_LE)
        assert read_report_text(PROFITABLE).lstrip().startswith("<!DOCTYPE html")

    def test_settings(self, profitable):
        s = profitable.settings
        assert (s.expert, s.symbol, s.timeframe) == ("Moving Average", "EURUSD@", "H1")
        assert (s.date_from, s.date_to) == (date(2025, 1, 1), date(2026, 1, 1))
        assert s.inputs == {
            "MaximumRisk": "0.02",
            "DecreaseFactor": "3.0",
            "MovingPeriod": "12",
            "MovingShift": "6",
        }
        assert (s.company, s.currency, s.deposit, s.leverage) == (
            "WM Markets Ltd",
            "USD",
            10000.0,
            100,
        )
        assert (s.server, s.build) == ("WMMarkets-Demo", 6249)

    def test_summary(self, profitable):
        summary = profitable.summary
        assert summary["Total Net Profit"] == "989.59"
        assert summary["Total Trades"] == "267"
        assert summary["Total Deals"] == "534"
        assert summary["Balance Drawdown Maximal"] == "684.68 (5.87%)"
        assert summary["Equity Drawdown Relative"] == "7.50% (842.03)"
        assert summary["Ticks"] == "1452013"
        assert summary["Largest profit trade"] == "640.76"
        assert summary["Average loss trade"] == "-18.14"
        assert summary["Maximal consecutive loss (count)"] == "-209.23 (3)"
        assert summary["Average consecutive losses"] == "3"
        assert len(summary) == 46

    def test_deals(self, profitable):
        deals = profitable.deals
        assert len(deals) == 535
        first, second, last = deals[0], deals[1], deals[-1]
        assert (first.type, first.profit, first.balance, first.symbol) == (
            "balance",
            10000.0,
            10000.0,
            None,
        )
        assert second.server_time == datetime(2025, 1, 2, 12, 0)
        assert (second.type, second.entry, second.volume, second.price) == (
            "sell",
            "in",
            0.19,
            1.03512,
        )
        assert last.comment == "end of test"
        assert last.server_time == datetime(2025, 12, 31, 22, 59, 59)
        assert last.balance == 10989.59
        assert profitable.trade_count == 267

    def test_money_adds_up(self, profitable):
        net = sum(d.profit + d.commission + d.swap for d in profitable.deals if d.type != "balance")
        assert net == pytest.approx(989.59, abs=0.005)
        assert sum(d.swap for d in profitable.deals) == pytest.approx(-82.88, abs=0.005)

    def test_orders(self, profitable):
        orders = profitable.orders
        assert len(orders) == 534
        assert orders[0].ticket == 2
        assert (orders[0].type, orders[0].state) == ("sell", "filled")
        assert (orders[0].volume_initial, orders[0].volume_filled) == (0.19, 0.19)
        assert orders[0].done_server_time == datetime(2025, 1, 2, 12, 0)

    def test_balance_is_rebuilt_from_deals(self, profitable):
        points, notes = rebuild_balance(profitable.deals)
        assert notes == []
        assert len(points) == len(profitable.deals)
        assert points[-1].balance == 10989.59
        assert [p.balance for p in points] == [d.balance for d in profitable.deals]

    def test_balance_mismatch_is_reported(self, profitable):
        deals = list(profitable.deals)
        deals[5] = deals[5].model_copy(update={"profit": deals[5].profit + 1})
        _, notes = rebuild_balance(deals)
        assert len(notes) == 1
        assert f"deal {deals[5].ticket}" in notes[0]


class TestNoTradesReport:
    def test_only_the_deposit(self, no_trades):
        assert no_trades.trade_count == 0
        assert [d.type for d in no_trades.deals] == ["balance"]
        assert no_trades.orders == []
        assert no_trades.settings.date_to == date(2025, 6, 4)

    def test_result_has_a_flat_balance(self, no_trades):
        result = to_result(
            no_trades,
            model=TickModel.OHLC_M1,
            fidelity="test",
            strategy_hash="abc",
            clock=fixed_clock(),
        )
        assert result.trade_count == 0
        assert [p.balance for p in result.balance] == [10000.0]


class TestNormalisedResult:
    def test_times_are_converted_with_the_measured_offsets(self, profitable):
        result = to_result(
            profitable,
            model=TickModel.OHLC_M1,
            fidelity="tester",
            strategy_hash="2110ef2fc64740ed",
            clock=fixed_clock(),
        )
        meta = result.meta
        assert meta.engine is Engine.MT5_TESTER
        assert meta.model is TickModel.OHLC_M1
        assert (meta.symbol, meta.timeframe, meta.leverage, meta.deposit) == (
            "EURUSD@",
            "H1",
            100,
            10000.0,
        )
        assert meta.server_utc_offsets_h == [2, 3]
        winter = result.deals[1]
        assert winter.time == datetime(2025, 1, 2, 10, 0, tzinfo=UTC)
        summer = next(d for d in result.deals if d.server_time.month == 7)
        assert summer.time == (summer.server_time - timedelta(hours=3)).replace(tzinfo=UTC)
        assert result.balance[-1].balance == 10989.59
        assert result.reported["Total Net Profit"] == "989.59"
        assert meta.notes == []

    def test_without_a_clock_times_stay_server_only(self, profitable):
        result = to_result(
            profitable, model=TickModel.OHLC_M1, fidelity="", strategy_hash=None, clock=None
        )
        assert all(d.time is None for d in result.deals)
        assert any("Server UTC offset unknown" in n for n in result.meta.notes)

    def test_disagreement_with_the_request_is_noted(self, profitable):
        result = to_result(
            profitable,
            model=TickModel.OHLC_M1,
            fidelity="",
            strategy_hash=None,
            clock=fixed_clock(),
            expected={"symbol": "EURUSD@", "leverage": 500},
        )
        assert result.meta.leverage == 100
        assert any("leverage 500" in note for note in result.meta.notes)

    def test_result_round_trips_as_json(self, profitable):
        result = to_result(
            profitable,
            model=TickModel.OHLC_M1,
            fidelity="",
            strategy_hash=None,
            clock=fixed_clock(),
        )
        again = type(result).model_validate_json(result.model_dump_json())
        assert again == result


class TestOtherLanguages:
    def test_labels_do_not_matter_for_settings_and_tables(self):
        html = read_report_text(PROFITABLE)
        for english, other in [
            ("Expert:", "Experte:"),
            ("Symbol:", "Symbol:"),
            ("Period:", "Zeitrahmen:"),
            ("Inputs:", "Eingaben:"),
            ("Company:", "Firma:"),
            ("Initial Deposit:", "Ersteinlage:"),
            (">Orders<", ">Aufträge<"),
            (">Deals<", ">Geschäfte<"),
            (">Settings<", ">Einstellungen<"),
            (">Results<", ">Ergebnisse<"),
        ]:
            html = html.replace(english, other)
        parsed = parse_report_html(html)
        assert parsed.settings.expert == "Moving Average"
        assert parsed.settings.deposit == 10000.0
        assert parsed.settings.inputs["MovingPeriod"] == "12"
        assert len(parsed.deals) == 535
        assert len(parsed.orders) == 534


class TestRejects:
    def test_not_a_report(self):
        with pytest.raises(ReportError):
            parse_report_html("<html><body><p>nothing</p></body></html>")

    def test_missing_deals(self):
        html = read_report_text(NO_TRADES)
        first_table_end = html.index("</table>") + len("</table>")
        with pytest.raises(ReportError, match="no Deals table"):
            parse_report_html(html[:first_table_end] + "</div></body></html>")

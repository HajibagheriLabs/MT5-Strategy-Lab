import codecs
from pathlib import Path

import pytest

from strategylab.backtest import prepare_parameters
from strategylab.compiler import stage_files
from strategylab.params import (
    NO_SOURCE_NOTE,
    ParamsError,
    build_set,
    discover,
    discover_inputs,
    parse_enums,
    parse_inputs,
    parse_set_text,
    read_set,
    validate_value,
)

MQL5 = Path(__file__).parent / "fixtures" / "mql5"
ALL_INPUTS = MQL5 / "AllInputs.mq5"
# Both written by the Strategy Tester itself, not by StrategyLab.
ALL_INPUTS_SET = MQL5 / "AllInputs.set"
MOVING_AVERAGE_SET = MQL5 / "Moving Average.set"
MOVING_AVERAGE_INPUTS = """
input double MaximumRisk        = 0.02;    // Maximum Risk in percentage
input double DecreaseFactor     = 3;       // Descrease factor
input int    MovingPeriod       = 12;      // Moving Average period
input int    MovingShift        = 6;       // Moving Average shift
"""


@pytest.fixture(scope="module")
def all_inputs():
    return {p.name: p for p in discover_inputs(ALL_INPUTS)}


def rendered(set_file):
    return [line if isinstance(line, str) else line.render() for line in set_file.lines]


class TestSourceParsing:
    @pytest.mark.parametrize(
        ("name", "kind", "value"),
        [
            ("FastPeriod", "integer", "12"),
            ("SlowPeriod", "integer", "26"),
            ("SignalFrame", "enum", "16388"),
            ("EntryMode", "enum", "1"),
            ("MaMethod", "enum", "1"),
            ("Threshold", "real", "-0.5"),
            ("Lots", "real", "0.1"),
            ("Sizing", "enum", "10"),
            ("UseTrailing", "bool", "true"),
            ("MaxVolume", "integer", "5000000000"),
            ("Retries", "integer", "3"),
            ("MagicNumber", "integer", "18446744073709551615"),
            ("Offset", "integer", "-7"),
            ("Steps", "integer", "200"),
            ("Shift", "integer", "-300"),
            ("Window", "integer", "60000"),
            ("Ratio", "real", "1.5"),
            ("TradeComment", "string", 'StrategyLab; "quoted"'),
            ("StartTime", "datetime", "1735787045"),
            ("LineColor", "color", "16748574"),
            ("FillColor", "color", "33023"),
            ("ReportEvery", "integer", "100"),
            ("First", "integer", "1"),
            ("Second", "integer", "2"),
        ],
    )
    def test_every_type_resolves_to_what_the_tester_writes(self, all_inputs, name, kind, value):
        param = all_inputs[name]
        assert (param.kind, param.value) == (kind, value)

    def test_order_labels_groups_and_lines(self, all_inputs):
        names = list(all_inputs)
        assert names[:3] == ["FastPeriod", "SlowPeriod", "SignalFrame"]
        assert names[-2:] == ["First", "Second"]
        assert all_inputs["FastPeriod"].label == "Fast MA period"
        assert all_inputs["MaMethod"].label is None
        assert all_inputs["FastPeriod"].group == "Signal"
        assert all_inputs["Lots"].group == "Money"
        assert all_inputs["TradeComment"].group == "Other"
        assert all_inputs["FastPeriod"].line == 19
        assert all_inputs["Second"].line == 45
        assert all_inputs["Second"].label == "Two at once"

    def test_sinput_is_static(self, all_inputs):
        assert all_inputs["ReportEvery"].static
        assert not all_inputs["FastPeriod"].static

    def test_enum_members_from_the_same_file(self, all_inputs):
        members = all_inputs["EntryMode"].members
        assert [(m.name, m.value, m.label) for m in members] == [
            ("MODE_CROSS", 0, "Crossing"),
            ("MODE_TOUCH", 1, "Touching"),
            ("MODE_BREAK", 5, "Breakout"),
            ("MODE_LAST", 6, "After breakout"),
        ]
        assert [(m.name, m.value) for m in all_inputs["Sizing"].members] == [
            ("SIZE_FIXED", 0),
            ("SIZE_RISK", 10),
        ]

    def test_standard_enum_members(self, all_inputs):
        frames = {m.name: m.value for m in all_inputs["SignalFrame"].members}
        assert frames["PERIOD_H1"] == 16385
        assert frames["PERIOD_MN1"] == 49153

    def test_source_details_that_must_not_confuse_the_parser(self):
        source = """
/* input int Hidden = 1; */
// input int AlsoHidden = 2;
input string Url = "http://example.com/a;b"; // Where to post
input int
      Spread = 30;   // Spread limit
input int Derived = FastPeriod * 2;
int inputs_seen = 0;
"""
        params, _ = parse_inputs(source)
        by_name = {p.name: p for p in params}
        assert list(by_name) == ["Url", "Spread", "Derived"]
        assert by_name["Url"].value == "http://example.com/a;b"
        assert by_name["Url"].label == "Where to post"
        assert by_name["Spread"].value == "30"
        assert by_name["Spread"].label == "Spread limit"
        assert by_name["Derived"].value is None
        assert by_name["Derived"].default == "FastPeriod * 2"

    def test_enum_values_that_follow_other_members(self):
        enums = parse_enums("enum E { A = 0x10, B, C = A, D };")
        assert [(m.name, m.value) for m in enums["E"]] == [
            ("A", 16),
            ("B", 17),
            ("C", 16),
            ("D", 17),
        ]

    def test_utf16_source(self, tmp_path):
        path = tmp_path / "ea.mq5"
        path.write_bytes(
            codecs.BOM_UTF16_LE + "input int Período = 5; // Período\r\n".encode("utf-16-le")
        )
        (param,) = discover_inputs(path)
        assert (param.value, param.label) == ("5", "Período")

    def test_inputs_in_bundled_headers_keep_source_order(self, tmp_path):
        (tmp_path / "Include" / "Lib").mkdir(parents=True)
        (tmp_path / "Include" / "Lib" / "risk.mqh").write_text(
            'enum RISK { LOW, HIGH };\ninput group "Risk"\ninput RISK Level = HIGH;\n'
        )
        (tmp_path / "ea.mq5").write_text(
            "input int Before = 1;\n"
            '#include "Include\\Lib\\risk.mqh"\n'
            "#include <Trade\\Trade.mqh>\n"
            "input int After = 2;\n"
        )
        params = discover_inputs(tmp_path / "ea.mq5", tmp_path)
        assert [(p.name, p.value, p.group, p.line) for p in params] == [
            ("Before", "1", None, 1),
            ("Level", "1", "Risk", 3),
            ("After", "2", "Risk", 4),
        ]

    def test_headers_outside_the_upload_are_not_read(self, tmp_path):
        outside = tmp_path / "outside.mqh"
        outside.write_text("input int Secret = 1;\n")
        upload = tmp_path / "upload"
        upload.mkdir()
        (upload / "ea.mq5").write_text('#include "../outside.mqh"\ninput int Mine = 2;\n')
        assert [p.name for p in discover_inputs(upload / "ea.mq5", upload)] == ["Mine"]


class TestSetFiles:
    @pytest.mark.parametrize("path", [ALL_INPUTS_SET, MOVING_AVERAGE_SET])
    def test_tester_files_round_trip_byte_for_byte(self, path):
        assert read_set(path).to_bytes() == path.read_bytes()

    def test_tester_file_layout(self):
        raw = ALL_INPUTS_SET.read_bytes()
        assert raw.startswith(codecs.BOM_UTF16_LE)
        assert "\r\n" in raw.decode("utf-16")
        entries = {e.name: e for e in read_set(ALL_INPUTS_SET).entries}
        assert entries["FastPeriod"].render() == "FastPeriod=12||12||1||120||N"
        assert (entries["TradeComment"].value, entries["TradeComment"].start) == (
            'StrategyLab; "quoted"',
            None,
        )

    def test_generated_file_matches_what_the_tester_wrote(self):
        ours = build_set(discover_inputs(ALL_INPUTS))
        tester = read_set(ALL_INPUTS_SET)
        # The tester's first three lines are a "saved automatically on <time>" header.
        assert rendered(ours) == rendered(tester)[3:]

    def test_overrides_keep_the_range_of_the_compiled_default(self):
        params, _ = parse_inputs(MOVING_AVERAGE_INPUTS)
        ours = build_set(params, {"MovingPeriod": "24", "MovingShift": "3"})
        assert [e.render() for e in ours.entries] == [
            e.render() for e in read_set(MOVING_AVERAGE_SET).entries
        ]

    def test_title_and_unresolved_inputs(self):
        params, _ = parse_inputs("input int A = 1;\ninput int B = A + 1;\n")
        set_file = build_set(params, title="input parameters for Test")
        assert rendered(set_file) == ["; input parameters for Test", ";", "A=1||1||1||10||N"]

    def test_unknown_override_is_rejected(self):
        params, _ = parse_inputs(MOVING_AVERAGE_INPUTS)
        with pytest.raises(ParamsError, match="no inputs named Nope"):
            build_set(params, {"Nope": "1"})

    def test_plain_lines_are_read_too(self):
        set_file = parse_set_text("; comment\r\nA=1\r\nB=2||2||1||20||Y\r\n")
        assert set_file.values() == {"A": "1", "B": "2"}
        assert set_file.entries[1].optimize is True
        set_file.set_value("A", "5")
        set_file.set_value("C", "x")
        assert set_file.to_text() == "; comment\r\nA=5\r\nB=2||2||1||20||Y\r\nC=x\r\n"


class TestValidation:
    @pytest.mark.parametrize(
        ("name", "given", "expected"),
        [
            ("FastPeriod", " 20 ", "20"),
            ("Lots", "1", "1.0"),
            ("UseTrailing", "FALSE", "false"),
            ("EntryMode", "MODE_BREAK", "5"),
            ("EntryMode", "5", "5"),
            ("SignalFrame", "PERIOD_D1", "16408"),
            ("StartTime", "D'2025.06.01 00:00'", "1748736000"),
            ("LineColor", "clrRed", "255"),
            ("TradeComment", " keep spaces ", " keep spaces "),
        ],
    )
    def test_values_are_normalised(self, all_inputs, name, given, expected):
        assert validate_value(all_inputs[name], given) == expected

    @pytest.mark.parametrize(
        ("name", "given", "message"),
        [
            ("FastPeriod", "1.5", "whole number"),
            ("Lots", "lots", "needs a number"),
            ("UseTrailing", "yes", "true or false"),
            ("EntryMode", "MODE_NONE", "must be one of MODE_CROSS"),
            ("EntryMode", "3", "must be one of"),
            ("LineColor", "reddish", "could not be read"),
            ("TradeComment", "two\nlines", "line break"),
        ],
    )
    def test_bad_values_are_explained(self, all_inputs, name, given, message):
        with pytest.raises(ParamsError, match=message):
            validate_value(all_inputs[name], given)


class TestDiscovery:
    def test_from_source(self, tmp_path):
        found = discover(ALL_INPUTS, MQL5)
        assert found.source == "source"
        assert len(found.params) == 24
        assert found.note is None

    def test_bare_ex5_says_inputs_cannot_be_read(self, tmp_path):
        found = discover(tmp_path / "ea.ex5", tmp_path)
        assert (found.source, found.params) == ("none", [])
        assert found.note == NO_SOURCE_NOTE
        assert ".set file" in found.note

    def test_ex5_with_a_set_file(self):
        found = discover(MQL5 / "AllInputs.ex5", MQL5, ALL_INPUTS_SET)
        assert found.source == "set_file"
        assert {p.name: p.value for p in found.params}["Lots"] == "0.1"


class TestPreparingARun:
    def test_source_overrides_are_validated_and_written_in_full(self, tmp_path):
        staged = stage_files({"ea.mq5": MOVING_AVERAGE_INPUTS.encode()}, "mq5", "ea.mq5", tmp_path)
        set_file, values, notes = prepare_parameters(staged, {"MovingPeriod": " 24 "})
        assert values == {"MovingPeriod": "24"}
        assert notes == []
        assert "MovingPeriod=24||12||1||120||N" in set_file.to_text()

    def test_unknown_input_names_the_real_ones(self, tmp_path):
        staged = stage_files({"ea.mq5": MOVING_AVERAGE_INPUTS.encode()}, "mq5", "ea.mq5", tmp_path)
        with pytest.raises(ParamsError, match="Its inputs are: MaximumRisk, DecreaseFactor"):
            prepare_parameters(staged, {"Period": "5"})

    def test_prebuilt_with_shipped_set_file(self, tmp_path):
        staged = stage_files(
            {"bin/ea.ex5": b"EX5", "bin/ea.set": ALL_INPUTS_SET.read_bytes()},
            "zip",
            "ea.zip",
            tmp_path,
        )
        set_file, values, notes = prepare_parameters(staged, {"Lots": "0.5"})
        assert set_file.values()["Lots"] == "0.5"
        assert set_file.values()["FastPeriod"] == "12"
        assert values == {"Lots": "0.5"}
        assert "from the uploaded .set file" in notes[0]

    def test_bare_prebuilt_passes_values_through(self, tmp_path):
        staged = stage_files({"ea.ex5": b"EX5"}, "ex5", "ea.ex5", tmp_path)
        set_file, values, notes = prepare_parameters(staged, {"Anything": "1"})
        assert set_file is None
        assert values == {"Anything": "1"}
        assert notes == [NO_SOURCE_NOTE]

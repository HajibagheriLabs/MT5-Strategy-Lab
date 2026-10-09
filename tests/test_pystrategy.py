import ast
import textwrap

import pytest

from strategylab.pystrategy import (
    ScriptError,
    apply_constants,
    load_script,
    read_script,
    run_inputs,
    stage_script,
)

SCRIPT = textwrap.dedent(
    """
    import argparse

    import MetaTrader5 as mt5

    SYMBOL = "EURUSD"  # traded symbol
    FAST_PERIOD = 12
    LOTS: float = 0.1
    OFFSET = -5
    USE_FILTER = True
    TIMEFRAME = mt5.TIMEFRAME_H1
    lowercase = 3
    COUNTER = 0
    COUNTER = 1
    A, B = 1, 2


    def main():
        parser = argparse.ArgumentParser()
        parser.add_argument("--slow", type=int, default=26, help="slow period")
        parser.add_argument("-r", "--risk", default=0.5)
        parser.add_argument("--mode", choices=["fast", "safe"], default="safe")
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("symbol_name")
        return parser.parse_args()
    """
).encode()


def inputs_by_name():
    diagnostics, inputs = read_script(SCRIPT, "s.py")
    assert diagnostics == []
    return {item.name: item for item in inputs}


def test_constants_are_read_with_their_types_and_labels():
    found = inputs_by_name()
    constants = {
        n: (i.kind, i.default, i.label) for n, i in found.items() if i.source == "constant"
    }
    assert constants == {
        "SYMBOL": ("string", "EURUSD", "traded symbol"),
        "FAST_PERIOD": ("integer", "12", None),
        "LOTS": ("real", "0.1", None),
        "OFFSET": ("integer", "-5", None),
        "USE_FILTER": ("bool", "true", None),
    }


def test_argparse_options_are_read():
    found = inputs_by_name()
    options = {n: i for n, i in found.items() if i.source == "option"}
    assert list(options) == ["--slow", "--risk", "--mode", "--dry-run"]
    assert (options["--slow"].kind, options["--slow"].default) == ("integer", "26")
    assert options["--slow"].label == "slow period"
    assert (options["--risk"].kind, options["--risk"].default) == ("real", "0.5")
    assert (options["--mode"].kind, options["--mode"].choices) == ("enum", ("fast", "safe"))
    assert options["--mode"].value_kind == "string"
    assert (options["--dry-run"].kind, options["--dry-run"].default) == ("bool", "false")


def test_a_syntax_error_says_where():
    diagnostics, inputs = read_script(b"x = 1\nif x\n    pass\n", "broken.py")
    assert inputs == []
    (error,) = diagnostics
    assert (error.severity, error.file, error.line) == ("error", "broken.py", 2)
    assert error.column is not None


def test_text_that_is_not_utf8_is_refused():
    (error,) = read_script(b"x = '\xff'\n", "latin.py")[0]
    assert error.severity == "error"
    assert "not UTF-8" in error.message


def test_a_script_without_metatrader5_gets_a_warning():
    (warning,) = read_script(b"print('hi')\n", "plain.py")[0]
    assert warning.severity == "warning"
    assert "MetaTrader5" in warning.message


def test_only_changed_values_are_applied():
    found = inputs_by_name()
    constants, args = run_inputs(
        list(found.values()),
        {
            "FAST_PERIOD": "20",
            "SYMBOL": "EURUSD",
            "USE_FILTER": "false",
            "--slow": "26",
            "--risk": "1.5",
            "--dry-run": "true",
            "--mode": "fast",
        },
    )
    assert constants == {"FAST_PERIOD": 20, "USE_FILTER": False}
    assert args == ["--risk", "1.5", "--dry-run", "--mode", "fast"]


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ({"NOPE": "1"}, "no inputs named NOPE"),
        ({"FAST_PERIOD": "1.5"}, "whole number"),
        ({"LOTS": "lots"}, "needs a number"),
        ({"USE_FILTER": "yes"}, "true or false"),
        ({"--mode": "wild"}, "must be one of fast, safe"),
        ({"SYMBOL": "EUR\nUSD"}, "line break"),
    ],
)
def test_bad_values_are_refused(values, message):
    with pytest.raises(ScriptError, match=message):
        run_inputs(list(inputs_by_name().values()), values)


def test_apply_constants_replaces_only_the_named_literals():
    tree = ast.parse("A = 1\nB: int = 2\nC = 3\n")
    apply_constants(tree, {"A": 10, "B": 20})
    namespace: dict = {}
    exec(compile(tree, "t.py", "exec"), namespace)
    assert (namespace["A"], namespace["B"], namespace["C"]) == (10, 20, 3)


def test_staging_names_the_folder_by_content(tmp_path):
    first = stage_script(SCRIPT, "my strategy.py", tmp_path)
    again = stage_script(SCRIPT, "my strategy.py", tmp_path)
    other = stage_script(SCRIPT + b"\n", "my strategy.py", tmp_path)
    assert first.hash == again.hash != other.hash
    assert first.entry_path.read_bytes() == SCRIPT
    assert first.ok
    reloaded = load_script(first.folder)
    assert (reloaded.hash, reloaded.entry, reloaded.inputs) == (
        first.hash,
        first.entry,
        first.inputs,
    )


def test_staging_refuses_what_is_not_a_script(tmp_path):
    with pytest.raises(ScriptError, match="not a Python script"):
        stage_script(b"x", "strategy.mq5", tmp_path)
    with pytest.raises(ScriptError, match="at most"):
        stage_script(b"#" * (3 * 1024 * 1024), "big.py", tmp_path)

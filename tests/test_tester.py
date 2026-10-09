import codecs
import re
import shutil
import subprocess
import threading
import time
from datetime import date
from pathlib import Path, PureWindowsPath

import pytest

from strategylab import tester
from strategylab.config import TerminalInstall, TerminalRunningError
from strategylab.result import TickModel
from strategylab.tester import (
    BacktestSpec,
    LogCursor,
    Outcome,
    SpecError,
    build_config,
    expert_setting,
    explain_failure,
    launch_command,
    parameters_text,
    run_tester,
    signed_exit_code,
)

REPORTS = Path(__file__).parent / "fixtures" / "reports"
# As the terminal wrote it when asked to test a symbol the server does not have.
SYMBOL_SHUTDOWN = (
    "DS\t0\t05:03:38.094\tTerminal\tshutdown with -1000012358 (tester symbol does not exist)"
)


def spec(**changes):
    values = dict(
        expert=r"StrategyLab\2110ef2fc64740ed\Moving Average.ex5",
        symbol="EURUSD@",
        timeframe="H1",
        date_from=date(2025, 1, 1),
        date_to=date(2026, 1, 1),
    )
    values.update(changes)
    return BacktestSpec(**values)


@pytest.fixture
def install(tmp_path):
    root = tmp_path / "MT5-Lab"
    for folder in ("MQL5/Experts", "MQL5/Profiles", "logs", "Tester/logs"):
        (root / folder).mkdir(parents=True)
    (root / "terminal64.exe").write_bytes(b"")
    (root / "MetaEditor64.exe").write_bytes(b"")
    return TerminalInstall(install_dir=root, data_dir=root, portable=True)


def utf16(text: str) -> bytes:
    return text.encode("utf-16-le")


def append_journal(path: Path, lines: list[str]) -> None:
    fresh = not path.exists()
    with path.open("ab") as handle:
        if fresh:
            handle.write(codecs.BOM_UTF16_LE)
        handle.write(utf16("".join(line + "\r\n" for line in lines)))


class FakeTerminal:
    """Plays the terminal: reads the generated config and leaves behind what a real run would."""

    def __init__(self, install, report=None, journal=(), exit_code=0, read_config=True, hang=False):
        self.install = install
        self.report = report
        self.journal = list(journal)
        self.exit_code = exit_code
        self.read_config = read_config
        self.hang = hang
        self.command = None
        self.config = None
        self.pid = 4242

    def __call__(self, command):
        self.command = command
        return self

    def wait(self, timeout=None):
        if self.hang:
            time.sleep(min(timeout or 0, 0.01))
            raise subprocess.TimeoutExpired(self.command, timeout)
        config_path = Path(re.search(r'/config:"([^"]+)"', self.command).group(1))
        self.config = config_path.read_bytes().decode("utf-16")
        lines = []
        if self.read_config:
            lines.append(f"AA\t0\t10:00:00.000\tTerminal\tlaunched with {config_path}")
        lines += self.journal
        append_journal(self.install.data_dir / "logs" / "20251009.log", lines)
        if self.report:
            report_rel = re.search(r"^Report=(.+)$", self.config, re.M).group(1).strip()
            target = self.install.data_dir / Path(*PureWindowsPath(report_rel).parts)
            shutil.copyfile(self.report, target.with_suffix(".htm"))
            target.with_name("report.png").write_bytes(b"PNG")
        return self.exit_code


class TestSpec:
    def test_valid(self):
        spec().validate()

    @pytest.mark.parametrize(
        ("changes", "message"),
        [
            ({"timeframe": "H5"}, "not a tester timeframe"),
            ({"date_to": date(2025, 1, 1)}, "end date must be after"),
            ({"deposit": 0}, "deposit must be positive"),
            ({"currency": "US"}, "three-letter"),
            ({"leverage": 0}, "Leverage"),
            ({"symbol": "EUR=USD"}, "not a symbol"),
            ({"parameters": {"bad name": "1"}}, "not an MQL5 input name"),
            ({"parameters": {"Lots": "1\n[Tester]"}}, "line break"),
        ],
    )
    def test_invalid(self, changes, message):
        with pytest.raises(SpecError, match=message):
            spec(**changes).validate()


class TestConfig:
    def test_documented_keys_and_values(self):
        text = build_config(
            spec(model=TickModel.REAL_TICKS, deposit=2500.5, leverage=500, currency="eur"),
            PureWindowsPath(r"StrategyLab\reports\run1\report"),
            "strategylab-run1.set",
        )
        assert text.endswith("\r\n")
        lines = text.split("\r\n")
        assert lines[:4] == ["[Experts]", "AllowLiveTrading=0", "AllowDllImport=0", "Enabled=0"]
        tester_section = dict(
            line.split("=", 1) for line in lines[lines.index("[Tester]") + 1 : -1]
        )
        assert tester_section == {
            "Expert": r"StrategyLab\2110ef2fc64740ed\Moving Average",
            "ExpertParameters": "strategylab-run1.set",
            "Symbol": "EURUSD@",
            "Period": "H1",
            "Model": "4",
            "ExecutionMode": "0",
            "Optimization": "0",
            "ForwardMode": "0",
            "FromDate": "2025.01.01",
            "ToDate": "2026.01.01",
            "Deposit": "2500.5",
            "Currency": "EUR",
            "Leverage": "1:500",
            "Report": r"StrategyLab\reports\run1\report",
            "ReplaceReport": "1",
            "ShutdownTerminal": "1",
            "Visual": "0",
            "UseLocal": "1",
            "UseRemote": "0",
            "UseCloud": "0",
        }

    @pytest.mark.parametrize(
        ("model", "code"),
        [
            (TickModel.EVERY_TICK, "0"),
            (TickModel.OHLC_M1, "1"),
            (TickModel.OPEN_PRICES, "2"),
            (TickModel.REAL_TICKS, "4"),
        ],
    )
    def test_model_codes(self, model, code):
        text = build_config(spec(model=model), PureWindowsPath("r"), "p.set")
        assert f"\r\nModel={code}\r\n" in text

    def test_expert_setting_drops_the_extension(self):
        assert expert_setting("StrategyLab/abc/My EA.ex5") == r"StrategyLab\abc\My EA"
        assert expert_setting(r"Examples\MACD\MACD Sample") == r"Examples\MACD\MACD Sample"

    def test_parameters_file(self):
        assert parameters_text({"MovingPeriod": "24", "Lots": "0.1"}) == (
            "MovingPeriod=24\r\nLots=0.1\r\n"
        )
        assert parameters_text({}) == ""

    def test_launch_command(self, install):
        config = Path(r"C:\runs\a b\tester.ini")
        assert launch_command(install, config) == (
            f'"{install.terminal_exe}" /portable /config:"{config}"'
        )
        regular = TerminalInstall(install.install_dir, install.data_dir, portable=False)
        assert "/portable" not in launch_command(regular, config)


class TestJournal:
    def test_signed_exit_codes(self):
        assert signed_exit_code(0) == 0
        assert signed_exit_code(3294954938) == -1000012358
        assert signed_exit_code(None) is None

    def test_explain_failure_uses_the_terminals_words(self):
        journal = "\r\n".join(
            [
                "GR\t2\t05:03:36.610\tTester\tsymbol NOPE not exist",
                "CH\t2\t05:03:36.610\tTerminal\ttester didn't start",
                "CN\t2\t05:03:37.945\tVirtual Hosting\tfailed to get list of virtual hosts",
                SYMBOL_SHUTDOWN,
            ]
        )
        assert explain_failure({"terminal": journal}) == (
            "symbol NOPE not exist; tester symbol does not exist"
        )

    def test_explain_failure_with_nothing_to_say(self):
        assert explain_failure({"terminal": "IR\t0\t05:02:04\tTerminal\tstarted"}) is None

    def test_cursor_returns_only_new_lines(self, install):
        journal = install.data_dir / "logs" / "20251009.log"
        append_journal(journal, ["old line"])
        cursor = LogCursor.start(install.data_dir)
        append_journal(journal, ["new line"])
        agent = install.data_dir / "Tester" / "Agent-127.0.0.1-3000" / "logs" / "20251009.log"
        agent.parent.mkdir(parents=True)
        append_journal(agent, ["agent line"])
        collected = cursor.collect()
        assert collected["terminal"] == "new line\r\n"
        assert collected["agent"] == "agent line\r\n"
        assert collected["tester"] == ""


class TestRun:
    def run(self, install, tmp_path, fake, **kwargs):
        return run_tester(spec(), install, tmp_path / "run", run_id="run1", launcher=fake, **kwargs)

    def test_success(self, install, tmp_path):
        fake = FakeTerminal(
            install,
            report=REPORTS / "ma_eurusd_h1_2025.htm",
            journal=["BB\t0\t10:00:09.000\tTerminal\tshutdown with 0"],
        )
        run = self.run(install, tmp_path, fake)
        assert run.outcome is Outcome.SUCCESS
        assert run.message == "267 trades."
        assert run.exit_code == 0
        assert run.report_path == tmp_path / "run" / "report" / "report.htm"
        assert (tmp_path / "run" / "report" / "report.png").is_file()
        assert run.parsed.trade_count == 267
        assert "shutdown with 0" in run.log_paths["terminal"].read_text(encoding="utf-8")
        assert "ExpertParameters=strategylab-run1.set" in fake.config
        assert "/portable" in fake.command

    def test_run_leaves_nothing_behind_in_the_terminal(self, install, tmp_path):
        fake = FakeTerminal(install, report=REPORTS / "ma_eurusd_h1_2025.htm")
        self.run(install, tmp_path, fake)
        assert not (install.mql5_dir / "Profiles" / "Tester" / "strategylab-run1.set").exists()
        assert not (install.data_dir / "StrategyLab" / "reports" / "run1").exists()

    def test_parameters_are_written_for_the_tester(self, install, tmp_path):
        captured = {}

        class Capturing(FakeTerminal):
            def wait(self, timeout=None):
                path = install.mql5_dir / "Profiles" / "Tester" / "strategylab-run1.set"
                captured["set"] = path.read_bytes()
                return super().wait(timeout)

        fake = Capturing(install, report=REPORTS / "ma_eurusd_h1_2025.htm")
        run_tester(
            spec(parameters={"MovingPeriod": "24"}),
            install,
            tmp_path / "run",
            run_id="run1",
            launcher=fake,
        )
        assert captured["set"] == codecs.BOM_UTF16_LE + utf16("MovingPeriod=24\r\n")

    def test_zero_trades(self, install, tmp_path):
        fake = FakeTerminal(install, report=REPORTS / "ma_no_trades.htm")
        run = self.run(install, tmp_path, fake)
        assert run.outcome is Outcome.ZERO_TRADES
        assert "no trades" in run.message

    def test_no_report_explains_why(self, install, tmp_path):
        fake = FakeTerminal(
            install,
            journal=[
                "GR\t2\t05:03:36.610\tTester\tsymbol NOPE not exist",
                SYMBOL_SHUTDOWN,
            ],
            exit_code=3294954938,
        )
        run = self.run(install, tmp_path, fake)
        assert run.outcome is Outcome.NO_REPORT
        assert run.exit_code == -1000012358
        assert "symbol NOPE not exist" in run.message

    def test_unreadable_report(self, install, tmp_path):
        broken = tmp_path / "broken.htm"
        broken.write_bytes(codecs.BOM_UTF16_LE + utf16("<html><body>half</body></html>"))
        run = self.run(install, tmp_path, FakeTerminal(install, report=broken))
        assert run.outcome is Outcome.NO_REPORT
        assert "could not be read" in run.message

    def test_timeout_stops_the_terminal(self, install, tmp_path):
        stopped = []

        def stopper(pid, grace):
            stopped.append(pid)
            return "closed"

        run = self.run(
            install, tmp_path, FakeTerminal(install, hang=True), timeout_s=1, stopper=stopper
        )
        assert run.outcome is Outcome.TIMEOUT
        assert stopped == [4242]
        assert run.stop == "closed"
        assert "within 1 s" in run.message
        assert not (install.mql5_dir / "Profiles" / "Tester" / "strategylab-run1.set").exists()

    def test_cancel_closes_the_terminal(self, install, tmp_path):
        stopped = []
        cancel = threading.Event()
        cancel.set()
        run = self.run(
            install,
            tmp_path,
            FakeTerminal(install, hang=True),
            cancel=cancel,
            stopper=lambda pid, grace: stopped.append(pid) or "killed",
        )
        assert run.outcome is Outcome.CANCELLED
        assert stopped == [4242]
        assert run.message == "Cancelled; the terminal was killed after not closing."
        assert run.parsed is None

    def test_journal_lines_are_followed_while_the_terminal_runs(self, install, tmp_path):
        agent_log = install.data_dir / "Tester" / "Agent-127.0.0.1-3000" / "logs" / "1.log"
        agent_log.parent.mkdir(parents=True)

        class Slow(FakeTerminal):
            calls = 0

            def wait(self, timeout=None):
                self.calls += 1
                if self.calls == 1:
                    append_journal(agent_log, ["CS\t0\t10:00:01.000\tTester\tpassed 10%"])
                    # Half a line: it is only reported once it is complete.
                    with agent_log.open("ab") as handle:
                        handle.write(utf16("CS\t0\t10:00:02.000\tTester\tfin"))
                    raise subprocess.TimeoutExpired(self.command, timeout)
                with agent_log.open("ab") as handle:
                    handle.write(utf16("al balance\r\n"))
                return super().wait(timeout)

        seen, parsing = [], []
        run = self.run(
            install,
            tmp_path,
            Slow(install, report=REPORTS / "ma_eurusd_h1_2025.htm"),
            on_log=lambda kind, line: seen.append((kind, line)),
            on_parsing=lambda: parsing.append(True),
        )
        assert run.outcome is Outcome.SUCCESS
        assert ("agent", "CS\t0\t10:00:01.000\tTester\tpassed 10%") in seen
        assert ("agent", "CS\t0\t10:00:02.000\tTester\tfinal balance") in seen
        assert any(kind == "terminal" and "launched with" in line for kind, line in seen)
        assert parsing == [True]

    def test_launch_swallowed_by_a_running_terminal(self, install, tmp_path):
        run = self.run(install, tmp_path, FakeTerminal(install, read_config=False))
        assert run.outcome is Outcome.TERMINAL_RUNNING
        assert "already running" in run.message

    def test_running_terminal_is_detected_before_launch(self, install, tmp_path, monkeypatch):
        def busy(_install):
            raise TerminalRunningError("MetaTrader 5 is already running (PID 99).")

        monkeypatch.setattr(tester, "ensure_terminal_idle", busy)

        def never(command):
            raise AssertionError("must not launch")

        run = self.run(install, tmp_path, never)
        assert run.outcome is Outcome.TERMINAL_RUNNING
        assert "PID 99" in run.message

    def test_invalid_spec_is_rejected_before_anything_happens(self, install, tmp_path):
        with pytest.raises(SpecError):
            run_tester(spec(timeframe="H7"), install, tmp_path / "run", launcher=None)
        assert not (tmp_path / "run").exists()

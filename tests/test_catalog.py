import threading
from datetime import UTC, date, datetime, timedelta

import pytest

from strategylab.catalog import HistoryCatalog
from strategylab.config import TerminalInstall

NOW = datetime(2026, 1, 10, 12, tzinfo=UTC)


def epoch(*parts):
    return int(datetime(*parts, tzinfo=UTC).timestamp())


class Measurer:
    def __init__(self):
        self.calls: list[dict[str, int]] = []
        self.fail = False

    def __call__(self, install, first_years):
        self.calls.append(dict(first_years))
        if self.fail:
            raise RuntimeError("terminal did not start")
        return "Demo", [
            {
                "name": name,
                "description": name,
                "digits": 5,
                "path": f"FX\\{name}",
                "first_bar": epoch(year, 3, 4),
                "last_bar": epoch(2026, 1, 9, 23, 59),
            }
            for name, year in first_years.items()
        ]


@pytest.fixture
def install(tmp_path):
    for symbol, years in {"EURUSD@": (2021, 2022, 2026), "GBPUSD@": (2024, 2026)}.items():
        for year in years:
            path = tmp_path / "bases" / "Demo" / "history" / symbol / f"{year}.hcc"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"x")
    ticks = tmp_path / "bases" / "Demo" / "ticks" / "EURUSD@"
    ticks.mkdir(parents=True)
    for month in ("202502", "202501", "notes"):
        (ticks / f"{month}.tkc").write_bytes(b"x")
    return TerminalInstall(tmp_path, tmp_path, True)


def catalog(install, tmp_path, measurer, clock=lambda: NOW, lock=None):
    return HistoryCatalog(
        install, None, tmp_path / "cache", lock or threading.Lock(), measurer, clock
    )


def test_symbols_and_their_dates(install, tmp_path):
    measurer = Measurer()
    view = catalog(install, tmp_path, measurer).view()
    assert view.server == "Demo"
    assert [s.name for s in view.symbols] == ["EURUSD@", "GBPUSD@"]
    eurusd = view.get("EURUSD@")
    assert eurusd.years == (2021, 2022, 2026)
    assert (eurusd.bars_from, eurusd.bars_to) == (date(2021, 3, 4), date(2026, 1, 9))
    assert eurusd.tick_months == ("2025-01", "2025-02")
    assert eurusd.ticks_from == date(2025, 1, 1)
    assert view.get("GBPUSD@").ticks_from is None
    assert measurer.calls == [{"EURUSD@": 2021, "GBPUSD@": 2024}]
    assert view.pending == [] and view.message is None


def test_measurements_are_reused_until_the_history_changes_or_a_day_passes(install, tmp_path):
    measurer = Measurer()
    now = [NOW]
    lab = catalog(install, tmp_path, measurer, clock=lambda: now[0])
    lab.view()
    lab.view()
    assert len(measurer.calls) == 1
    older = install.data_dir / "bases" / "Demo" / "history" / "GBPUSD@" / "2019.hcc"
    older.write_bytes(b"x")
    lab.view()
    assert measurer.calls[-1] == {"GBPUSD@": 2019}
    now[0] = NOW + timedelta(days=2)
    lab.view(only=["EURUSD@"])
    assert measurer.calls[-1] == {"EURUSD@": 2021}
    lab.view(refresh=True)
    assert measurer.calls[-1] == {"EURUSD@": 2021, "GBPUSD@": 2019}


def test_a_busy_terminal_is_not_waited_for(install, tmp_path):
    lock = threading.Lock()
    measurer = Measurer()
    lock.acquire()
    view = catalog(install, tmp_path, measurer, lock=lock).view()
    assert measurer.calls == []
    assert view.pending == ["EURUSD@", "GBPUSD@"]
    assert "busy with a run" in view.message
    assert view.get("EURUSD@").years == (2021, 2022, 2026)
    assert not view.get("EURUSD@").measured


def test_a_failed_measurement_keeps_what_was_known(install, tmp_path):
    measurer = Measurer()
    now = [NOW]
    lab = catalog(install, tmp_path, measurer, clock=lambda: now[0])
    lab.view()
    measurer.fail = True
    now[0] = NOW + timedelta(days=2)
    view = lab.view()
    assert "could not be measured: terminal did not start" in view.message
    assert view.get("EURUSD@").bars_from == date(2021, 3, 4)


def test_several_servers_need_one_to_be_named(install, tmp_path):
    (install.data_dir / "bases" / "Other" / "history").mkdir(parents=True)
    view = catalog(install, tmp_path, Measurer()).view()
    assert view.symbols == []
    assert "set [account] server" in view.message

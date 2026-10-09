import codecs
from pathlib import Path

import pytest

from strategylab.config import (
    AmbiguousTerminalError,
    DataFolderNotFoundError,
    LocalConfigError,
    MetaEditorNotFoundError,
    ProcessInfo,
    TerminalNotFoundError,
    TerminalRunningError,
    decode_mt_text,
    discover_candidates,
    ensure_terminal_idle,
    load_settings,
    terminals_using,
)


def make_install(folder: Path, *, started_portable: bool = False, metaeditor: bool = True) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "terminal64.exe").write_bytes(b"")
    if metaeditor:
        (folder / "MetaEditor64.exe").write_bytes(b"")
    if started_portable:
        (folder / "MQL5").mkdir()
    return folder


def make_data_folder(appdata: Path, instance_id: str, install: Path) -> Path:
    folder = appdata / "MetaQuotes" / "Terminal" / instance_id
    (folder / "MQL5").mkdir(parents=True)
    # MetaTrader writes origin.txt as UTF-16LE with a BOM.
    (folder / "origin.txt").write_bytes(codecs.BOM_UTF16_LE + str(install).encode("utf-16-le"))
    return folder


def write_config(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


@pytest.fixture
def appdata(tmp_path):
    folder = tmp_path / "AppData" / "Roaming"
    folder.mkdir(parents=True)
    return folder


@pytest.fixture
def no_config(tmp_path):
    return tmp_path / "absent.toml"


class TestDecode:
    def test_utf16_with_bom(self):
        assert decode_mt_text(codecs.BOM_UTF16_LE + "C:\\MT5".encode("utf-16-le")) == "C:\\MT5"

    def test_utf16_without_bom(self):
        assert decode_mt_text("C:\\MT5".encode("utf-16-le")) == "C:\\MT5"

    def test_utf8_with_bom(self):
        assert decode_mt_text(codecs.BOM_UTF8 + "Ä".encode()) == "Ä"

    def test_cp1252_fallback(self):
        assert decode_mt_text("café".encode("cp1252")) == "café"


class TestLocalConfig:
    def test_portable_install(self, tmp_path, appdata):
        install = make_install(tmp_path / "MT5-Lab", started_portable=True)
        config = write_config(
            tmp_path / "local.toml",
            f"[terminal]\npath = '{install}'\nportable = true\n[account]\nserver = 'Demo'\n",
        )
        settings = load_settings(config, appdata=appdata, search_roots=[])
        assert settings.terminal.install_dir == install
        assert settings.terminal.data_dir == install
        assert settings.terminal.portable is True
        assert settings.terminal.metaeditor_exe == install / "MetaEditor64.exe"
        assert settings.account_server == "Demo"
        assert settings.source == str(config)

    def test_path_may_name_the_executable(self, tmp_path, appdata):
        install = make_install(tmp_path / "MT5-Lab", started_portable=True)
        config = write_config(
            tmp_path / "local.toml", f"[terminal]\npath = '{install / 'terminal64.exe'}'\n"
        )
        settings = load_settings(config, appdata=appdata, search_roots=[])
        assert settings.terminal.install_dir == install

    def test_portable_is_inferred_from_mql5_folder(self, tmp_path, appdata):
        install = make_install(tmp_path / "MT5-Lab", started_portable=True)
        config = write_config(tmp_path / "local.toml", f"[terminal]\npath = '{install}'\n")
        settings = load_settings(config, appdata=appdata, search_roots=[])
        assert settings.terminal.portable is True

    def test_missing_folder(self, tmp_path, appdata):
        config = write_config(
            tmp_path / "local.toml", f"[terminal]\npath = '{tmp_path / 'nowhere'}'\n"
        )
        with pytest.raises(TerminalNotFoundError, match="does not exist"):
            load_settings(config, appdata=appdata, search_roots=[])

    def test_folder_without_terminal(self, tmp_path, appdata):
        (tmp_path / "empty").mkdir()
        config = write_config(
            tmp_path / "local.toml", f"[terminal]\npath = '{tmp_path / 'empty'}'\n"
        )
        with pytest.raises(TerminalNotFoundError, match=r"does not contain terminal64\.exe"):
            load_settings(config, appdata=appdata, search_roots=[])

    def test_missing_metaeditor(self, tmp_path, appdata):
        install = make_install(tmp_path / "MT5", started_portable=True, metaeditor=False)
        config = write_config(tmp_path / "local.toml", f"[terminal]\npath = '{install}'\n")
        with pytest.raises(MetaEditorNotFoundError, match=r"MetaEditor64\.exe"):
            load_settings(config, appdata=appdata, search_roots=[])

    def test_portable_never_started(self, tmp_path, appdata):
        install = make_install(tmp_path / "MT5")
        config = write_config(
            tmp_path / "local.toml", f"[terminal]\npath = '{install}'\nportable = true\n"
        )
        with pytest.raises(DataFolderNotFoundError, match="/portable"):
            load_settings(config, appdata=appdata, search_roots=[])

    def test_non_portable_uses_origin_txt(self, tmp_path, appdata):
        install = make_install(tmp_path / "Program Files" / "Broker MT5")
        data = make_data_folder(appdata, "0123ABCD", install)
        config = write_config(
            tmp_path / "local.toml", f"[terminal]\npath = '{install}'\nportable = false\n"
        )
        settings = load_settings(config, appdata=appdata, search_roots=[])
        assert settings.terminal.data_dir == data
        assert settings.terminal.portable is False
        assert any("not a portable copy" in note for note in settings.notes)

    def test_non_portable_without_data_folder(self, tmp_path, appdata):
        install = make_install(tmp_path / "Broker MT5")
        config = write_config(
            tmp_path / "local.toml", f"[terminal]\npath = '{install}'\nportable = false\n"
        )
        with pytest.raises(DataFolderNotFoundError, match="data_dir"):
            load_settings(config, appdata=appdata, search_roots=[])

    def test_data_dir_override(self, tmp_path, appdata):
        install = make_install(tmp_path / "Broker MT5")
        data = tmp_path / "elsewhere"
        (data / "MQL5").mkdir(parents=True)
        config = write_config(
            tmp_path / "local.toml",
            f"[terminal]\npath = '{install}'\nportable = false\ndata_dir = '{data}'\n",
        )
        settings = load_settings(config, appdata=appdata, search_roots=[])
        assert settings.terminal.data_dir == data

    def test_invalid_toml_mentions_quoting(self, tmp_path, appdata):
        config = write_config(tmp_path / "local.toml", '[terminal]\npath = "C:\\MT5-Lab"\n')
        with pytest.raises(LocalConfigError, match="single quotes"):
            load_settings(config, appdata=appdata, search_roots=[])

    def test_unknown_key_lists_known_keys(self, tmp_path, appdata):
        config = write_config(tmp_path / "local.toml", "[terminal]\nportible = true\n")
        with pytest.raises(LocalConfigError, match="Known keys: data_dir, path, portable"):
            load_settings(config, appdata=appdata, search_roots=[])

    def test_unknown_section(self, tmp_path, appdata):
        config = write_config(tmp_path / "local.toml", "[trading]\nlots = 1\n")
        with pytest.raises(LocalConfigError, match=r"unknown section \[trading\]"):
            load_settings(config, appdata=appdata, search_roots=[])

    def test_wrong_type(self, tmp_path, appdata):
        config = write_config(tmp_path / "local.toml", "[terminal]\nportable = 'yes'\n")
        with pytest.raises(LocalConfigError, match="'portable' must be a bool"):
            load_settings(config, appdata=appdata, search_roots=[])

    def test_server_without_history_is_noted(self, tmp_path, appdata):
        install = make_install(tmp_path / "MT5-Lab", started_portable=True)
        (install / "bases" / "Default").mkdir(parents=True)
        config = write_config(
            tmp_path / "local.toml",
            f"[terminal]\npath = '{install}'\n[account]\nserver = 'Broker-Demo'\n",
        )
        settings = load_settings(config, appdata=appdata, search_roots=[])
        assert any("Broker-Demo" in note for note in settings.notes)
        (install / "bases" / "Broker-Demo").mkdir()
        settings = load_settings(config, appdata=appdata, search_roots=[])
        assert settings.notes == ()
        assert settings.terminal.history_servers() == ["Broker-Demo"]

    def test_workspace_is_relative_to_config(self, tmp_path, appdata):
        install = make_install(tmp_path / "MT5-Lab", started_portable=True)
        config = write_config(
            tmp_path / "local.toml",
            f"[terminal]\npath = '{install}'\n[workspace]\ndir = 'runs'\n",
        )
        settings = load_settings(config, appdata=appdata, search_roots=[])
        assert settings.workspace_dir == tmp_path / "runs"


class TestDiscovery:
    def test_prefers_the_portable_copy(self, tmp_path, appdata, no_config):
        roots = tmp_path / "roots"
        trading = make_install(roots / "Broker MT5")
        make_data_folder(appdata, "AAAA", trading)
        lab = make_install(roots / "MT5-Lab", started_portable=True)
        settings = load_settings(no_config, appdata=appdata, search_roots=[roots])
        assert settings.terminal.install_dir == lab
        assert settings.terminal.portable is True
        assert settings.source.startswith("auto-discovery")

    def test_installs_found_only_through_origin_txt(self, tmp_path, appdata, no_config):
        install = make_install(tmp_path / "Odd Place" / "MT5")
        data = make_data_folder(appdata, "BBBB", install)
        settings = load_settings(no_config, appdata=appdata, search_roots=[])
        assert settings.terminal.install_dir == install
        assert settings.terminal.data_dir == data

    def test_stale_origin_is_ignored(self, tmp_path, appdata, no_config):
        make_data_folder(appdata, "CCCC", tmp_path / "Uninstalled MT5")
        with pytest.raises(TerminalNotFoundError, match="No MetaTrader 5 install was found"):
            load_settings(no_config, appdata=appdata, search_roots=[])

    def test_two_portable_copies_are_ambiguous(self, tmp_path, appdata, no_config):
        roots = tmp_path / "roots"
        make_install(roots / "Lab-A", started_portable=True)
        make_install(roots / "Lab-B", started_portable=True)
        with pytest.raises(AmbiguousTerminalError, match=r"Found 2 MetaTrader 5 installs"):
            load_settings(no_config, appdata=appdata, search_roots=[roots])

    def test_never_started_install(self, tmp_path, appdata, no_config):
        roots = tmp_path / "roots"
        make_install(roots / "Fresh MT5")
        with pytest.raises(DataFolderNotFoundError, match="never been started"):
            load_settings(no_config, appdata=appdata, search_roots=[roots])

    def test_candidates_are_unique(self, tmp_path, appdata):
        roots = tmp_path / "roots"
        install = make_install(roots / "Broker MT5")
        make_data_folder(appdata, "DDDD", install)
        candidates = discover_candidates(appdata, [roots, roots])
        assert [c.install_dir for c in candidates] == [install]

    def test_config_without_terminal_section_still_discovers(self, tmp_path, appdata):
        roots = tmp_path / "roots"
        lab = make_install(roots / "MT5-Lab", started_portable=True)
        config = write_config(tmp_path / "local.toml", "[account]\nserver = 'Demo'\n")
        settings = load_settings(config, appdata=appdata, search_roots=[roots])
        assert settings.terminal.install_dir == lab
        assert "terminal auto-discovered" in settings.source


class TestRunningDetection:
    @pytest.fixture
    def lab(self, tmp_path, appdata):
        install = make_install(tmp_path / "MT5-Lab", started_portable=True)
        config = write_config(tmp_path / "local.toml", f"[terminal]\npath = '{install}'\n")
        return load_settings(config, appdata=appdata, search_roots=[]).terminal

    def proc(self, pid, exe, *args):
        return ProcessInfo(pid=pid, name="terminal64.exe", exe=exe, cmdline=(str(exe), *args))

    def test_portable_process_on_same_folder(self, lab, appdata):
        procs = [self.proc(10, lab.terminal_exe, "/portable")]
        assert [r.pid for r in terminals_using(lab, procs, appdata)] == [10]
        with pytest.raises(TerminalRunningError, match="PID 10"):
            ensure_terminal_idle(lab, procs, appdata)

    def test_same_exe_without_portable_uses_another_folder(self, lab, appdata, tmp_path):
        make_data_folder(appdata, "EEEE", lab.install_dir)
        procs = [self.proc(11, lab.terminal_exe)]
        assert terminals_using(lab, procs, appdata) == []
        ensure_terminal_idle(lab, procs, appdata)

    def test_other_install_does_not_count(self, lab, appdata, tmp_path):
        other = make_install(tmp_path / "Broker MT5")
        make_data_folder(appdata, "FFFF", other)
        procs = [self.proc(12, other / "terminal64.exe")]
        assert terminals_using(lab, procs, appdata) == []

    def test_unknown_data_folder_matches_by_executable(self, lab, appdata):
        procs = [self.proc(13, lab.terminal_exe)]
        assert [r.pid for r in terminals_using(lab, procs, appdata)] == [13]

    def test_case_and_separators_do_not_matter(self, lab, appdata):
        exe = Path(str(lab.terminal_exe).upper().replace("\\", "/"))
        procs = [self.proc(14, exe, "/PORTABLE")]
        assert [r.pid for r in terminals_using(lab, procs, appdata)] == [14]

    def test_other_processes_are_ignored(self, lab, appdata):
        procs = [ProcessInfo(pid=15, name="metaeditor64.exe", exe=lab.metaeditor_exe, cmdline=())]
        assert terminals_using(lab, procs, appdata) == []


@pytest.mark.mt5
def test_real_settings_resolve():
    settings = load_settings()
    assert settings.terminal.terminal_exe.is_file()
    assert settings.terminal.metaeditor_exe.is_file()
    assert settings.terminal.mql5_dir.is_dir()

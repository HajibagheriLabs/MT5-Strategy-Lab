import codecs
import io
import re
import shutil
import zipfile
from pathlib import Path, PureWindowsPath

import pytest

from strategylab import compiler
from strategylab.compiler import (
    MetaEditorError,
    UploadError,
    build_command,
    compile_strategy,
    compile_upload,
    content_hash,
    parse_compile_log,
    read_compile_log,
    read_zip,
    rewrite_bundled_includes,
    stage_files,
    stage_upload,
)
from strategylab.config import TerminalInstall, load_settings

FIXTURES = Path(__file__).parent / "fixtures" / "compile"
# The folders the fixture logs were captured in.
CLEAN_FOLDER = PureWindowsPath(r"C:\MT5-Lab\MQL5\Experts\StrategyLab\2110ef2fc64740ed")
ERRORS_FOLDER = PureWindowsPath(r"C:\MT5-Lab\MQL5\Experts\StrategyLab\6db0a69d84a583b4")
CAPTURED_MQL5 = PureWindowsPath(r"C:\MT5-Lab\MQL5")

EA_SOURCE = (
    b"input int Period=10;\r\nint OnInit(){ return(INIT_SUCCEEDED); }\r\nvoid OnTick(){}\r\n"
)


def make_zip(entries: dict[str, bytes | str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return buffer.getvalue()


@pytest.fixture
def install(tmp_path):
    root = tmp_path / "MT5-Lab"
    (root / "MQL5" / "Experts").mkdir(parents=True)
    (root / "MetaEditor64.exe").write_bytes(b"build 1")
    (root / "terminal64.exe").write_bytes(b"")
    return TerminalInstall(install_dir=root, data_dir=root, portable=True)


class FakeMetaEditor:
    """Stands in for MetaEditor: writes a captured log and, for a clean compile, an .ex5."""

    def __init__(self, log_fixture="clean.log", write_ex5=True, write_log=True):
        self.log_fixture = log_fixture
        self.write_ex5 = write_ex5
        self.write_log = write_log
        self.commands = []

    def __call__(self, command, timeout):
        self.commands.append(command)
        source = Path(re.search(r'/compile:"([^"]+)"', command).group(1))
        log = Path(re.search(r'/log:"([^"]+)"', command).group(1))
        if self.write_log:
            shutil.copyfile(FIXTURES / self.log_fixture, log)
        if self.write_ex5:
            source.with_suffix(".ex5").write_bytes(b"EX5")
        return 1 if self.write_ex5 else 0


class TestLogParsing:
    def test_clean_compile(self):
        log = read_compile_log(FIXTURES / "clean.log", (CLEAN_FOLDER, CAPTURED_MQL5))
        assert log.has_result
        assert (log.error_count, log.warning_count) == (0, 0)
        assert log.elapsed_ms == 724
        assert log.diagnostics == ()

    def test_compile_with_errors(self):
        log = read_compile_log(FIXTURES / "errors.log", (ERRORS_FOLDER, CAPTURED_MQL5))
        assert (log.error_count, log.warning_count) == (10, 1)
        assert len(log.errors) == 10
        assert len(log.warnings) == 1
        first = log.errors[0]
        assert first.file == "Moving Average Errors.mq5"
        assert (first.line, first.column, first.code) == (62, 24, 256)
        assert first.message == "undeclared identifier 'undeclared_thing'"
        assert log.errors[1].line == 67
        assert log.errors[1].message == "'}' - semicolon expected"
        warning = log.warnings[0]
        assert (warning.line, warning.column, warning.code) == (174, 30, 95)
        assert warning.message == "expression has no effect"

    def test_error_without_a_file(self):
        log = read_compile_log(FIXTURES / "errors.log")
        orphan = [d for d in log.errors if d.code == 161]
        assert len(orphan) == 1
        assert orphan[0].file is None
        assert (orphan[0].line, orphan[0].column) == (1, 1)

    def test_paths_outside_known_roots_stay_absolute(self):
        log = read_compile_log(FIXTURES / "errors.log")
        assert log.errors[0].file == str(ERRORS_FOLDER / "Moving Average Errors.mq5")

    def test_fixture_encoding_is_what_metaeditor_writes(self):
        raw = (FIXTURES / "clean.log").read_bytes()
        assert raw.startswith(codecs.BOM_UTF16_LE)
        assert "\r\n" in raw.decode("utf-16")

    def test_parentheses_in_path_and_message(self):
        text = r"C:\EAs\My EA (v2)\x.mq5(3,4) : error 152: 'f(1,2)' - some operator expected"
        (diag,) = parse_compile_log(text + "\nResult: 1 errors, 0 warnings").diagnostics
        assert diag.file == r"C:\EAs\My EA (v2)\x.mq5"
        assert (diag.line, diag.column) == (3, 4)
        assert diag.message == "'f(1,2)' - some operator expected"

    def test_error_without_position(self):
        text = r"C:\x\a.mq5 : error 106: file 'b.mqh' not found"
        (diag,) = parse_compile_log(text).diagnostics
        assert (diag.file, diag.line, diag.column, diag.code) == (r"C:\x\a.mq5", None, None, 106)

    def test_information_lines_are_ignored(self):
        text = "\n".join(
            [
                r"C:\x\a.mq5 : information: compiling C:\x\a.mq5",
                r"C:\x\a.mq5 : information: including C:\x\error : warning 1: odd.mqh",
                " : information: generating code 50%",
                "Result: 0 errors, 0 warnings, 12 ms elapsed, cpu='X64 Regular'",
            ]
        )
        log = parse_compile_log(text)
        assert log.diagnostics == ()
        assert log.elapsed_ms == 12

    def test_missing_result_line(self):
        assert not parse_compile_log("C:\\x\\a.mq5 : information: compiling").has_result


class TestHashing:
    def test_same_content_same_hash(self):
        assert content_hash({"a.mq5": b"x"}) == content_hash({"a.mq5": b"x"})

    def test_content_and_names_both_count(self):
        base = content_hash({"a.mq5": b"x"})
        assert content_hash({"a.mq5": b"y"}) != base
        assert content_hash({"b.mq5": b"x"}) != base

    def test_boundaries_between_files_count(self):
        assert content_hash({"a": b"bc", "d": b""}) != content_hash({"a": b"b", "d": b"c"})

    def test_folder_is_named_by_hash(self, tmp_path, install):
        upload = tmp_path / "Trend EA.mq5"
        upload.write_bytes(EA_SOURCE)
        staged = stage_upload(upload, install.strategies_dir)
        assert staged.hash == content_hash({"Trend EA.mq5": EA_SOURCE})
        assert len(staged.hash) == 16
        assert staged.folder == install.strategies_dir / staged.hash
        assert (staged.folder / "Trend EA.mq5").read_bytes() == EA_SOURCE
        assert staged.expert_path(install) == rf"StrategyLab\{staged.hash}\Trend EA.ex5"

    def test_restaging_reuses_the_folder(self, tmp_path, install):
        upload = tmp_path / "ea.mq5"
        upload.write_bytes(EA_SOURCE)
        first = stage_upload(upload, install.strategies_dir)
        marker = first.folder / "marker"
        marker.write_text("kept")
        second = stage_upload(upload, install.strategies_dir)
        assert second == first
        assert marker.exists()

    def test_zip_wrapping_and_timestamps_do_not_change_the_hash(self):
        plain = read_zip(make_zip({"ea.mq5": EA_SOURCE, "lib.mqh": b"int f(){return 1;}"}))
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for name, content in plain.items():
                info = zipfile.ZipInfo(f"Wrapper/{name}", date_time=(2001, 2, 3, 4, 5, 6))
                archive.writestr(info, content)
        wrapped = read_zip(buffer.getvalue())
        assert content_hash(wrapped) == content_hash(plain)

    def test_upload_names_are_made_safe(self, tmp_path, install):
        upload = tmp_path / "ea.mq5"
        upload.write_bytes(EA_SOURCE)
        staged = stage_upload(upload, install.strategies_dir, upload_name="..\\evil;name=1.mq5")
        assert staged.entry == "evil_name_1.mq5"
        assert staged.entry_path.parent == staged.folder


class TestZipHandling:
    def test_ea_with_include_folder(self, tmp_path, install):
        data = make_zip({"MyEA/MyEA.mq5": EA_SOURCE, "MyEA/lib/util.mqh": b"int u(){return 1;}"})
        upload = tmp_path / "bundle.zip"
        upload.write_bytes(data)
        staged = stage_upload(upload, install.strategies_dir)
        assert staged.kind == "zip"
        assert staged.entry == "MyEA.mq5"
        assert staged.files == ("MyEA.mq5", "lib/util.mqh")
        assert (staged.folder / "lib" / "util.mqh").is_file()

    def test_mql5_tree_keeps_its_layout(self):
        files = read_zip(
            make_zip(
                {
                    "MQL5/Experts/Bot.mq5": EA_SOURCE,
                    "MQL5/Include/Lib/a.mqh": b"int a(){return 1;}",
                }
            )
        )
        assert set(files) == {"Experts/Bot.mq5", "Include/Lib/a.mqh"}
        assert compiler.choose_entry(files) == "Experts/Bot.mq5"

    def test_picks_the_expert_among_several_sources(self):
        script = b"void OnStart(){}\r\n"
        files = {"tool.mq5": script, "ea.mq5": EA_SOURCE}
        assert compiler.choose_entry(files) == "ea.mq5"

    def test_picks_an_expert_saved_as_utf16(self):
        utf16 = codecs.BOM_UTF16_LE + EA_SOURCE.decode().encode("utf-16-le")
        files = {"tool.mq5": b"void OnStart(){}", "ea.mq5": utf16}
        assert compiler.choose_entry(files) == "ea.mq5"

    def test_several_experts_are_rejected(self):
        with pytest.raises(UploadError, match="several Expert Advisors"):
            compiler.choose_entry({"a.mq5": EA_SOURCE, "b.mq5": EA_SOURCE})

    def test_headers_alone_are_rejected(self):
        with pytest.raises(UploadError, match=r"no \.mq5 source"):
            compiler.choose_entry({"Include/a.mqh": b""})

    def test_prebuilt_only(self):
        assert compiler.choose_entry({"bin/ea.ex5": b"EX5", "readme.txt": b""}) == "bin/ea.ex5"

    @pytest.mark.parametrize(
        "name", ["../evil.mq5", "a/../../evil.mq5", "/abs/evil.mq5", "C:/evil.mq5"]
    )
    def test_paths_escaping_the_folder_are_rejected(self, name):
        with pytest.raises(UploadError, match=r"outside the archive|absolute path"):
            read_zip(make_zip({name: EA_SOURCE}))

    def test_dlls_are_rejected(self):
        with pytest.raises(UploadError, match="DLLs and executables"):
            read_zip(make_zip({"ea.mq5": EA_SOURCE, "Libraries/helper.dll": b"MZ"}))

    def test_archive_junk_is_dropped(self):
        files = read_zip(
            make_zip({"ea.mq5": EA_SOURCE, "__MACOSX/._ea.mq5": b"x", ".DS_Store": b"x"})
        )
        assert set(files) == {"ea.mq5"}

    def test_not_a_zip(self):
        with pytest.raises(UploadError, match="not a valid zip"):
            read_zip(b"definitely not a zip")

    def test_empty_zip(self):
        with pytest.raises(UploadError, match="empty"):
            read_zip(make_zip({"__MACOSX/x": b""}))

    def test_too_many_files(self, monkeypatch):
        monkeypatch.setattr(compiler, "MAX_BUNDLE_FILES", 2)
        with pytest.raises(UploadError, match="limit is 2"):
            read_zip(make_zip({"a.mq5": b"", "b.mqh": b"", "c.mqh": b""}))

    def test_too_large(self, monkeypatch):
        monkeypatch.setattr(compiler, "MAX_BUNDLE_BYTES", 10)
        with pytest.raises(UploadError, match="unpacks to more than"):
            read_zip(make_zip({"a.mq5": b"x" * 11}))

    def test_case_clash(self):
        with pytest.raises(UploadError, match="twice"):
            read_zip(make_zip({"a/ea.mq5": EA_SOURCE, "A/EA.mq5": EA_SOURCE}))

    @pytest.mark.parametrize(
        ("name", "message"),
        [("ea.py", "Python strategy"), ("ea.txt", "not a supported upload")],
    )
    def test_unsupported_uploads(self, tmp_path, install, name, message):
        upload = tmp_path / name
        upload.write_bytes(b"")
        with pytest.raises(UploadError, match=message):
            stage_upload(upload, install.strategies_dir)


class TestIncludeRewriting:
    def test_bundled_headers_become_relative_quoted_includes(self):
        files = {
            "Experts/Bot.mq5": b"#include <Trade\\Trade.mqh>\r\n#include <Lib\\a.mqh>\r\n",
            "Include/Lib/a.mqh": b"  #include <Lib/b.mqh>\r\nint a(){return 1;}\r\n",
            "Include/Lib/b.mqh": b"int b(){return 2;}\r\n",
        }
        rewritten, notes = rewrite_bundled_includes(files)
        assert rewritten["Experts/Bot.mq5"] == (
            b'#include <Trade\\Trade.mqh>\r\n#include "..\\Include\\Lib\\a.mqh"\r\n'
        )
        assert rewritten["Include/Lib/a.mqh"].startswith(b'  #include "b.mqh"\r\n')
        assert rewritten["Include/Lib/b.mqh"] == files["Include/Lib/b.mqh"]
        assert len(notes) == 2

    def test_header_lookup_ignores_case(self):
        files = {"Bot.mq5": b"#include <lib\\A.MQH>\n", "Include/Lib/a.mqh": b""}
        rewritten, _ = rewrite_bundled_includes(files)
        assert rewritten["Bot.mq5"] == b'#include "Include\\Lib\\a.mqh"\n'

    def test_utf16_sources_keep_their_encoding(self):
        text = "#include <Lib\\a.mqh>\r\n// Grüße\r\n"
        raw = codecs.BOM_UTF16_LE + text.encode("utf-16-le")
        rewritten, _ = rewrite_bundled_includes({"Bot.mq5": raw, "Include/Lib/a.mqh": b""})
        out = rewritten["Bot.mq5"]
        assert out.startswith(codecs.BOM_UTF16_LE)
        assert out.decode("utf-16") == '#include "Include\\Lib\\a.mqh"\r\n// Grüße\r\n'

    def test_bundles_without_include_folder_are_untouched(self):
        files = {"Bot.mq5": b"#include <Lib\\a.mqh>\n", "Lib/a.mqh": b""}
        rewritten, notes = rewrite_bundled_includes(files)
        assert rewritten == files
        assert notes == []

    def test_staged_copy_is_rewritten_but_hash_is_of_the_upload(self, install):
        files = {"Bot.mq5": b"#include <Lib\\a.mqh>\n", "Include/Lib/a.mqh": b""}
        staged = stage_files(files, "zip", "bot.zip", install.strategies_dir)
        assert staged.hash == content_hash(files)
        assert (staged.folder / "Bot.mq5").read_bytes() == b'#include "Include\\Lib\\a.mqh"\n'
        assert staged.notes


class TestCompile:
    def stage(self, install, source=EA_SOURCE, name="ea.mq5"):
        return stage_files({name: source}, "mq5", name, install.strategies_dir)

    def test_command_line_quotes_values_after_the_colon(self, install):
        source = install.strategies_dir / "abc" / "My EA.mq5"
        log = install.strategies_dir / "abc" / "compile.log"
        command = build_command(install, source, log)
        assert command == (
            f'"{install.metaeditor_exe}" /portable /compile:"{source}" '
            f'/include:"{install.mql5_dir}" /log:"{log}"'
        )

    def test_non_portable_has_no_portable_switch(self, install):
        regular = TerminalInstall(install.install_dir, install.data_dir, portable=False)
        assert "/portable" not in build_command(regular, Path("a.mq5"), Path("a.log"))

    def test_clean_compile_then_cache(self, install):
        staged = self.stage(install)
        fake = FakeMetaEditor()
        first = compile_strategy(staged, install, runner=fake)
        assert first.ok and first.compiled and not first.cached
        assert first.errors == ()
        assert first.elapsed_ms == 724
        assert staged.ex5_path.is_file()
        second = compile_strategy(staged, install, runner=fake)
        assert second.ok and second.cached
        assert len(fake.commands) == 1
        compile_strategy(staged, install, runner=fake, force=True)
        assert len(fake.commands) == 2

    def test_failed_compile_is_cached_too(self, install):
        staged = self.stage(install)
        fake = FakeMetaEditor("errors.log", write_ex5=False)
        first = compile_strategy(staged, install, runner=fake)
        assert not first.ok
        assert len(first.errors) == 10
        second = compile_strategy(staged, install, runner=fake)
        assert second.cached and not second.ok
        assert second.errors == first.errors
        assert len(fake.commands) == 1

    def test_new_metaeditor_build_invalidates_the_cache(self, install):
        staged = self.stage(install)
        fake = FakeMetaEditor()
        compile_strategy(staged, install, runner=fake)
        install.metaeditor_exe.write_bytes(b"build 2, a different size")
        assert not compile_strategy(staged, install, runner=fake).cached
        assert len(fake.commands) == 2

    def test_stale_binary_is_never_reported_as_fresh(self, install):
        staged = self.stage(install)
        staged.ex5_path.write_bytes(b"OLD")
        result = compile_strategy(staged, install, runner=FakeMetaEditor("errors.log", False))
        assert not result.ok
        assert not staged.ex5_path.exists()

    def test_no_log_is_an_error(self, install):
        staged = self.stage(install)
        with pytest.raises(MetaEditorError, match="without writing a compile log"):
            compile_strategy(staged, install, runner=FakeMetaEditor(write_log=False))

    def test_log_without_result_is_an_error(self, install):
        staged = self.stage(install)

        def truncated(command, timeout):
            log = Path(re.search(r'/log:"([^"]+)"', command).group(1))
            log.write_bytes(codecs.BOM_UTF16_LE + "x : information: compiling".encode("utf-16-le"))
            return 0

        with pytest.raises(MetaEditorError, match="no result line"):
            compile_strategy(staged, install, runner=truncated)

    def test_no_errors_but_no_binary(self, install):
        staged = self.stage(install)
        result = compile_strategy(staged, install, runner=FakeMetaEditor(write_ex5=False))
        assert not result.ok
        assert any("no .ex5" in note for note in result.notes)

    def test_prebuilt_binary_is_not_compiled(self, tmp_path, install):
        upload = tmp_path / "ea.ex5"
        upload.write_bytes(b"EX5")

        def never(command, timeout):
            raise AssertionError("MetaEditor must not run for a prebuilt .ex5")

        result = compile_upload(upload, install, runner=never)
        assert result.ok and not result.compiled
        assert result.strategy.prebuilt
        assert result.strategy.kind == "ex5"


@pytest.mark.mt5
class TestRealMetaEditor:
    @pytest.fixture
    def terminal(self):
        return load_settings().terminal

    def moving_average(self, terminal):
        return terminal.experts_dir / "Examples" / "Moving Average" / "Moving Average.mq5"

    def test_moving_average_compiles(self, terminal):
        result = compile_upload(self.moving_average(terminal), terminal, force=True)
        assert result.ok, [str(d) for d in result.diagnostics]
        assert result.errors == ()
        assert result.strategy.ex5_path.is_file()

    def test_broken_copy_reports_the_line(self, tmp_path, terminal):
        lines = self.moving_average(terminal).read_bytes().split(b"\r\n")
        assert b"double profit=" in lines[61]
        lines[61] = lines[61].replace(b"double profit=", b"double profit=no_such_name+")
        broken = tmp_path / "Moving Average Broken.mq5"
        broken.write_bytes(b"\r\n".join(lines))
        result = compile_upload(broken, terminal, force=True)
        assert not result.ok
        first = result.errors[0]
        assert (first.file, first.line, first.code) == ("Moving Average Broken.mq5", 62, 256)
        assert "no_such_name" in first.message

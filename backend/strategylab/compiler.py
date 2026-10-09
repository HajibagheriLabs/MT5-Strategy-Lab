"""MetaEditor command-line compilation and compile log parsing.

Every upload is staged into its own folder, MQL5\\Experts\\StrategyLab\\<hash>\\, named by a hash
of its file names and bytes. The same upload always lands in the same folder, so it is compiled
once and the result is reused. The MetaEditor behaviour this module relies on was checked
against the real tool; see DECISIONS.md.
"""

from __future__ import annotations

import codecs
import hashlib
import io
import json
import posixpath
import re
import shutil
import subprocess
import threading
import time
import uuid
import zipfile
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath
from typing import Literal

from strategylab.config import TerminalInstall, decode_mt_text

HASH_LENGTH = 16
MANIFEST_NAME = "strategy.json"
LOG_NAME = "compile.log"
COMPILE_TIMEOUT_S = 120.0
MAX_BUNDLE_FILES = 2000
MAX_BUNDLE_BYTES = 64 * 1024 * 1024

UploadKind = Literal["mq5", "ex5", "zip"]
Severity = Literal["error", "warning"]

# Archive clutter added by operating systems and editors, never part of a strategy.
_JUNK_PARTS = {"__macosx", ".ds_store", "thumbs.db", "desktop.ini", ".git", ".vscode"}
# Native code would run inside the terminal with the user's rights, and the tester only loads
# DLLs when explicitly allowed; refusing them keeps uploads to MQL5 programs and their data.
_BLOCKED_SUFFIXES = {".dll", ".exe", ".bat", ".cmd", ".ps1", ".com", ".scr", ".msi", ".vbs"}
_WINDOWS_INVALID = re.compile(r'[<>:"|?*\x00-\x1f]')
_UNSAFE_NAME_CHARS = re.compile(r"[^\w .()\[\]+-]")
_ON_TICK = re.compile(rb"\bOnTick\s*\(|O\x00n\x00T\x00i\x00c\x00k\x00")
_ANGLE_INCLUDE = re.compile(r"^(?P<lead>[ \t]*#[ \t]*include[ \t]*)<(?P<target>[^>\r\n]+)>", re.M)

_DIAGNOSTIC = re.compile(
    r"^(?P<file>.*?)"
    r"(?:\((?P<line>\d+),(?P<column>\d+)\))?"
    r"\s*:\s*(?P<severity>error|warning)(?:\s+(?P<code>\d+))?\s*:\s*(?P<message>.*)$"
)
_RESULT = re.compile(
    r"^Result:\s*(?P<errors>\d+)\s+errors?,\s*(?P<warnings>\d+)\s+warnings?"
    r"(?:,\s*(?P<elapsed>\d+)\s*ms\s+elapsed)?"
)

# MetaEditor's command-line handling is not documented as safe to run concurrently.
_COMPILE_LOCK = threading.Lock()


class CompileError(Exception):
    """The upload could not be staged or MetaEditor could not be run; not a code error."""


class UploadError(CompileError):
    pass


class MetaEditorError(CompileError):
    pass


@dataclass(frozen=True)
class Diagnostic:
    severity: Severity
    message: str
    code: int | None = None
    file: str | None = None
    line: int | None = None
    column: int | None = None

    def __str__(self) -> str:
        where = self.file or "<unknown file>"
        if self.line is not None:
            where += f":{self.line}:{self.column}"
        code = f" {self.code}" if self.code is not None else ""
        return f"{where}: {self.severity}{code}: {self.message}"


@dataclass(frozen=True)
class CompileLog:
    diagnostics: tuple[Diagnostic, ...]
    error_count: int | None
    warning_count: int | None
    elapsed_ms: int | None

    @property
    def has_result(self) -> bool:
        return self.error_count is not None

    @property
    def errors(self) -> tuple[Diagnostic, ...]:
        return tuple(d for d in self.diagnostics if d.severity == "error")

    @property
    def warnings(self) -> tuple[Diagnostic, ...]:
        return tuple(d for d in self.diagnostics if d.severity == "warning")


@dataclass(frozen=True)
class StagedStrategy:
    hash: str
    kind: UploadKind
    folder: Path
    entry: str
    upload_name: str
    files: tuple[str, ...]
    notes: tuple[str, ...] = ()

    @property
    def prebuilt(self) -> bool:
        return self.entry.lower().endswith(".ex5")

    @property
    def entry_path(self) -> Path:
        return self.folder / self.entry

    @property
    def ex5_path(self) -> Path:
        return self.entry_path.with_suffix(".ex5")

    def expert_path(self, install: TerminalInstall) -> str:
        """The .ex5 relative to MQL5\\Experts, which is how the Strategy Tester names an expert."""
        return str(PureWindowsPath(self.ex5_path.relative_to(install.experts_dir)))


@dataclass(frozen=True)
class CompileResult:
    strategy: StagedStrategy
    ok: bool
    compiled: bool
    cached: bool
    diagnostics: tuple[Diagnostic, ...] = ()
    elapsed_ms: int | None = None
    log_path: Path | None = None
    notes: tuple[str, ...] = ()

    @property
    def errors(self) -> tuple[Diagnostic, ...]:
        return tuple(d for d in self.diagnostics if d.severity == "error")

    @property
    def warnings(self) -> tuple[Diagnostic, ...]:
        return tuple(d for d in self.diagnostics if d.severity == "warning")


# --- log parsing -------------------------------------------------------------------------------


def display_path(raw: str, roots: Iterable[PurePath]) -> str:
    """Shorten an absolute path from the log to one relative to the first root containing it."""
    path = PureWindowsPath(raw)
    for root in roots:
        try:
            return path.relative_to(PureWindowsPath(root)).as_posix()
        except ValueError:
            continue
    return raw


def parse_compile_log(text: str, roots: Iterable[PurePath] = ()) -> CompileLog:
    roots = tuple(roots)
    diagnostics: list[Diagnostic] = []
    error_count = warning_count = elapsed_ms = None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        result = _RESULT.match(line)
        if result:
            error_count = int(result["errors"])
            warning_count = int(result["warnings"])
            elapsed_ms = int(result["elapsed"]) if result["elapsed"] else None
            continue
        match = _DIAGNOSTIC.match(line)
        # Informational lines ("path : information: including ...") never match the severity
        # group, but a message could quote one; a real file part never contains " : ".
        if not match or " : " in match["file"]:
            continue
        file_part = match["file"].strip()
        diagnostics.append(
            Diagnostic(
                severity=match["severity"],
                message=match["message"].strip(),
                code=int(match["code"]) if match["code"] else None,
                file=display_path(file_part, roots) if file_part else None,
                line=int(match["line"]) if match["line"] else None,
                column=int(match["column"]) if match["column"] else None,
            )
        )
    return CompileLog(tuple(diagnostics), error_count, warning_count, elapsed_ms)


def read_compile_log(path: Path, roots: Iterable[PurePath] = ()) -> CompileLog:
    return parse_compile_log(decode_mt_text(path.read_bytes()), roots)


# --- staging -----------------------------------------------------------------------------------


def content_hash(files: Mapping[str, bytes]) -> str:
    digest = hashlib.sha256()
    for name in sorted(files):
        data = files[name]
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(len(data)).encode("ascii"))
        digest.update(b"\0")
        digest.update(data)
    return digest.hexdigest()[:HASH_LENGTH]


def safe_file_name(name: str) -> str:
    base = PureWindowsPath(PurePosixPath(name.replace("\\", "/")).name).name
    cleaned = _UNSAFE_NAME_CHARS.sub("_", base).strip(" .")
    if not cleaned or cleaned.startswith("."):
        raise UploadError(f"'{name}' is not a usable file name.")
    return cleaned


def _normalise_member(name: str) -> str | None:
    """Turn a zip member name into a safe relative POSIX path, or None for directories/junk."""
    if name.endswith(("/", "\\")):
        return None
    unified = name.replace("\\", "/")
    if unified.startswith("/") or re.match(r"^[A-Za-z]:", unified):
        raise UploadError(f"The zip entry '{name}' has an absolute path. Re-create the archive.")
    parts = [part for part in unified.split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise UploadError(f"The zip entry '{name}' points outside the archive.")
    if not parts or any(part.lower() in _JUNK_PARTS for part in parts):
        return None
    if any(_WINDOWS_INVALID.search(part) for part in parts):
        raise UploadError(f"The zip entry '{name}' has characters Windows cannot use in a path.")
    return "/".join(parts)


def _strip_wrappers(files: dict[str, bytes]) -> dict[str, bytes]:
    """Drop folders that wrap everything (MyEA/..., MQL5/...), keeping the layout below them."""
    while files:
        split = [name.split("/", 1) for name in files]
        if any(len(parts) == 1 for parts in split):
            break
        heads = {parts[0].lower() for parts in split}
        if len(heads) != 1:
            break
        files = {parts[1]: files[name] for name, parts in zip(files, split, strict=True)}
    return files


def read_zip(data: bytes) -> dict[str, bytes]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise UploadError("The upload is not a valid zip archive.") from exc
    with archive:
        members = [info for info in archive.infolist() if not info.is_dir()]
        if len(members) > MAX_BUNDLE_FILES:
            raise UploadError(
                f"The zip holds {len(members)} files; the limit is {MAX_BUNDLE_FILES}. Include "
                "only the EA and the files it needs."
            )
        files: dict[str, bytes] = {}
        total = 0
        for info in members:
            name = _normalise_member(info.filename)
            if name is None:
                continue
            if PurePosixPath(name).suffix.lower() in _BLOCKED_SUFFIXES:
                raise UploadError(
                    f"The zip contains '{name}'. DLLs and executables are not accepted; upload "
                    "the MQL5 sources, include files and data files only."
                )
            remaining = MAX_BUNDLE_BYTES - total
            # Read one byte past the budget so a member that lies about its size is caught.
            with archive.open(info) as handle:
                content = handle.read(remaining + 1)
            total += len(content)
            if total > MAX_BUNDLE_BYTES:
                raise UploadError(
                    f"The zip unpacks to more than {MAX_BUNDLE_BYTES // (1024 * 1024)} MB."
                )
            if name.lower() in {existing.lower() for existing in files}:
                raise UploadError(f"The zip contains '{name}' twice (names differ only by case).")
            files[name] = content
    if not files:
        raise UploadError("The zip is empty.")
    return _strip_wrappers(files)


def _is_under_include(name: str) -> bool:
    return name.lower().startswith("include/")


def choose_entry(files: Mapping[str, bytes]) -> str:
    sources = sorted(
        name for name in files if name.lower().endswith(".mq5") and not _is_under_include(name)
    )
    if len(sources) == 1:
        return sources[0]
    if len(sources) > 1:
        experts = [name for name in sources if _ON_TICK.search(files[name])]
        if len(experts) == 1:
            return experts[0]
        listed = ", ".join(experts or sources)
        raise UploadError(
            f"The zip contains several Expert Advisors ({listed}). Upload one EA per zip, with "
            "only the include files it needs."
        )
    binaries = sorted(name for name in files if name.lower().endswith(".ex5"))
    if len(binaries) == 1:
        return binaries[0]
    if binaries:
        raise UploadError(
            f"The zip contains no .mq5 source and several .ex5 files ({', '.join(binaries)}). "
            "Upload one compiled EA at a time."
        )
    raise UploadError("The zip contains no .mq5 source file and no compiled .ex5 file.")


def _detect_text_codec(raw: bytes) -> tuple[str, bytes]:
    if raw.startswith(codecs.BOM_UTF16_LE):
        return "utf-16-le", codecs.BOM_UTF16_LE
    if raw.startswith(codecs.BOM_UTF8):
        return "utf-8", codecs.BOM_UTF8
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError:
        return "cp1252", b""
    return "utf-8", b""


def rewrite_bundled_includes(files: Mapping[str, bytes]) -> tuple[dict[str, bytes], list[str]]:
    """Point `#include <x>` at headers shipped in the bundle's Include/ folder.

    MetaEditor resolves `<x>` against the data folder's MQL5\\Include, and from the main file
    only also against an Include\\ folder beside it; a header including another header the same
    way fails. Rewriting those directives as quoted paths relative to the including file makes
    the bundle compile in isolation without touching the shared Include folder. Directives for
    headers the bundle does not ship (the standard library) are left alone.
    """
    bundled = {name[len("include/") :].lower(): name for name in files if _is_under_include(name)}
    if not bundled:
        return dict(files), []

    rewritten: dict[str, bytes] = {}
    notes: list[str] = []
    for name, raw in files.items():
        if not name.lower().endswith((".mq5", ".mqh")):
            rewritten[name] = raw
            continue
        codec, bom = _detect_text_codec(raw)
        text = raw[len(bom) :].decode(codec)
        here = posixpath.dirname(name)

        def replace(match: re.Match[str], here: str = here, name: str = name) -> str:
            target = match["target"].strip().replace("\\", "/")
            shipped = bundled.get(target.lower())
            if shipped is None:
                return match.group(0)
            relative = posixpath.relpath(shipped, here or ".").replace("/", "\\")
            notes.append(f"{name}: #include <{match['target']}> now reads {shipped}")
            return f'{match["lead"]}"{relative}"'

        new_text = _ANGLE_INCLUDE.sub(replace, text)
        rewritten[name] = raw if new_text == text else bom + new_text.encode(codec)
    return rewritten, notes


def _manifest_path(folder: Path) -> Path:
    return folder / MANIFEST_NAME


def _read_manifest(folder: Path) -> dict[str, object] | None:
    try:
        return json.loads(_manifest_path(folder).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_manifest(folder: Path, manifest: Mapping[str, object]) -> None:
    target = _manifest_path(folder)
    temp = target.with_name(f"{target.name}.{uuid.uuid4().hex}.tmp")
    temp.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    temp.replace(target)


def _staged_from_manifest(folder: Path, manifest: Mapping[str, object]) -> StagedStrategy | None:
    try:
        staged = StagedStrategy(
            hash=str(manifest["hash"]),
            kind=manifest["kind"],
            folder=folder,
            entry=str(manifest["entry"]),
            upload_name=str(manifest["upload_name"]),
            files=tuple(manifest["files"]),
            notes=tuple(manifest.get("notes", ())),
        )
    except (KeyError, TypeError):
        return None
    if not all((folder / name).is_file() for name in staged.files):
        return None
    return staged


def stage_files(
    files: Mapping[str, bytes],
    kind: UploadKind,
    upload_name: str,
    strategies_dir: Path,
) -> StagedStrategy:
    digest = content_hash(files)
    folder = strategies_dir / digest
    manifest = _read_manifest(folder)
    if manifest is not None:
        existing = _staged_from_manifest(folder, manifest)
        if existing is not None:
            return existing

    entry = choose_entry(files)
    to_write, notes = rewrite_bundled_includes(files)
    staged = StagedStrategy(
        hash=digest,
        kind=kind,
        folder=folder,
        entry=entry,
        upload_name=upload_name,
        files=tuple(sorted(files)),
        notes=tuple(notes),
    )

    strategies_dir.mkdir(parents=True, exist_ok=True)
    # Build the folder under a temporary name and rename it into place, so a half-written
    # folder is never mistaken for a staged strategy.
    temp = strategies_dir / f".{digest}.{uuid.uuid4().hex[:8]}.tmp"
    try:
        for name, content in to_write.items():
            target = temp / Path(*name.split("/"))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        _write_manifest(
            temp,
            {
                "hash": staged.hash,
                "kind": staged.kind,
                "entry": staged.entry,
                "upload_name": staged.upload_name,
                "files": list(staged.files),
                "notes": list(staged.notes),
                "staged_at": datetime.now(UTC).isoformat(timespec="seconds"),
            },
        )
        if folder.exists():
            shutil.rmtree(folder)
        temp.rename(folder)
    finally:
        if temp.exists():
            shutil.rmtree(temp, ignore_errors=True)
    return staged


def stage_upload(
    upload: Path,
    strategies_dir: Path,
    upload_name: str | None = None,
) -> StagedStrategy:
    name = upload_name or upload.name
    suffix = PurePosixPath(name.replace("\\", "/")).suffix.lower()
    try:
        data = upload.read_bytes()
    except OSError as exc:
        raise UploadError(f"Cannot read {upload}: {exc}.") from exc
    if suffix == ".mq5":
        return stage_files({safe_file_name(name): data}, "mq5", name, strategies_dir)
    if suffix == ".ex5":
        return stage_files({safe_file_name(name): data}, "ex5", name, strategies_dir)
    if suffix == ".zip":
        return stage_files(read_zip(data), "zip", name, strategies_dir)
    if suffix == ".py":
        raise UploadError(
            f"'{name}' is a Python strategy. Those run in the Python engine and are not compiled."
        )
    raise UploadError(
        f"'{name}' is not a supported upload. Use an .mq5 source file, a compiled .ex5 file, or "
        "a .zip containing an EA and its include files."
    )


# --- compiling ---------------------------------------------------------------------------------

Runner = Callable[[str, float], int]


def build_command(install: TerminalInstall, source: Path, log: Path) -> str:
    """The MetaEditor command line, as a string.

    MetaEditor only recognises a switch whose value is quoted after the colon
    (/compile:"C:\\a b\\x.mq5"). Passing an argument list lets Python quote the whole switch
    instead ("/compile:C:\\a b\\x.mq5"), which MetaEditor silently ignores: it exits at once
    without compiling or writing a log. Windows paths cannot contain double quotes, so
    wrapping each value in them is safe.
    """
    parts = [f'"{install.metaeditor_exe}"']
    if install.portable:
        parts.append("/portable")
    parts += [
        f'/compile:"{source}"',
        # /include replaces the include root rather than adding to it; naming the data folder's
        # own MQL5 keeps the standard library and pins the root to the folder we resolved.
        f'/include:"{install.mql5_dir}"',
        f'/log:"{log}"',
    ]
    return " ".join(parts)


def run_metaeditor(command: str, timeout: float) -> int:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        completed = subprocess.run(
            command, capture_output=True, timeout=timeout, creationflags=flags, check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise MetaEditorError(
            f"MetaEditor did not finish within {timeout:.0f} s and was stopped. Check that it "
            "is not waiting on a dialog, then try again."
        ) from exc
    except OSError as exc:
        raise MetaEditorError(f"Could not start MetaEditor: {exc}.") from exc
    return completed.returncode


def toolchain_fingerprint(install: TerminalInstall) -> str:
    """Changes whenever MetaEditor is updated, so cached results from an older build are redone."""
    stat = install.metaeditor_exe.stat()
    return f"{stat.st_size}:{int(stat.st_mtime)}"


def _diagnostic_from_json(data: Mapping[str, object]) -> Diagnostic:
    return Diagnostic(**data)


def compile_strategy(
    staged: StagedStrategy,
    install: TerminalInstall,
    *,
    force: bool = False,
    timeout: float = COMPILE_TIMEOUT_S,
    runner: Runner = run_metaeditor,
) -> CompileResult:
    if staged.prebuilt:
        present = staged.entry_path.is_file()
        return CompileResult(
            strategy=staged,
            ok=present,
            compiled=False,
            cached=False,
            notes=(
                "Prebuilt .ex5: it was compiled elsewhere, so there is no source to check.",
                *staged.notes,
            ),
        )

    log_path = staged.folder / LOG_NAME
    fingerprint = toolchain_fingerprint(install)
    manifest = _read_manifest(staged.folder) or {}
    previous = manifest.get("compile")
    if (
        not force
        and isinstance(previous, dict)
        and previous.get("toolchain") == fingerprint
        and (not previous.get("ok") or staged.ex5_path.is_file())
    ):
        return CompileResult(
            strategy=staged,
            ok=bool(previous["ok"]),
            compiled=True,
            cached=True,
            diagnostics=tuple(_diagnostic_from_json(d) for d in previous.get("diagnostics", [])),
            elapsed_ms=previous.get("elapsed_ms"),
            log_path=log_path if log_path.is_file() else None,
            notes=tuple(previous.get("notes", [])),
        )

    with _COMPILE_LOCK:
        # A stale binary or log must never be read back as this compile's output.
        staged.ex5_path.unlink(missing_ok=True)
        log_path.unlink(missing_ok=True)
        started = time.monotonic()
        exit_code = runner(build_command(install, staged.entry_path, log_path), timeout)
        wall_ms = int((time.monotonic() - started) * 1000)

    if not log_path.is_file():
        raise MetaEditorError(
            f"MetaEditor exited (code {exit_code}) without writing a compile log for "
            f"{staged.entry}. Make sure {install.metaeditor_exe} runs when started by hand."
        )
    log = read_compile_log(log_path, (staged.folder, install.mql5_dir))
    if not log.has_result:
        raise MetaEditorError(
            f"The compile log for {staged.entry} has no result line, so MetaEditor stopped "
            f"before finishing. The log is at {log_path}."
        )

    notes = list(staged.notes)
    parsed_errors, parsed_warnings = len(log.errors), len(log.warnings)
    if (parsed_errors, parsed_warnings) != (log.error_count, log.warning_count):
        notes.append(
            f"MetaEditor reported {log.error_count} errors and {log.warning_count} warnings; "
            f"{parsed_errors} and {parsed_warnings} could be read from the log, see {LOG_NAME}."
        )
    ok = log.error_count == 0 and staged.ex5_path.is_file()
    if log.error_count == 0 and not ok:
        notes.append("MetaEditor reported no errors but produced no .ex5 file.")
    elapsed = log.elapsed_ms if log.elapsed_ms is not None else wall_ms

    manifest["compile"] = {
        "ok": ok,
        "toolchain": fingerprint,
        "exit_code": exit_code,
        "elapsed_ms": elapsed,
        "diagnostics": [asdict(d) for d in log.diagnostics],
        "notes": notes,
        "compiled_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    _write_manifest(staged.folder, manifest)
    return CompileResult(
        strategy=staged,
        ok=ok,
        compiled=True,
        cached=False,
        diagnostics=log.diagnostics,
        elapsed_ms=elapsed,
        log_path=log_path,
        notes=tuple(notes),
    )


def compile_upload(
    upload: Path,
    install: TerminalInstall,
    *,
    upload_name: str | None = None,
    force: bool = False,
    timeout: float = COMPILE_TIMEOUT_S,
    runner: Runner = run_metaeditor,
) -> CompileResult:
    staged = stage_upload(upload, install.strategies_dir, upload_name)
    return compile_strategy(staged, install, force=force, timeout=timeout, runner=runner)

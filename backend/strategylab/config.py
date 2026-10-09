"""Settings from local.toml and MetaTrader 5 terminal discovery.

A terminal is described by three things: the install folder (terminal64.exe and
MetaEditor64.exe), whether it runs in portable mode, and its data folder (MQL5\\, config\\,
logs\\, Tester\\, bases\\). In portable mode the data folder is the install folder. Otherwise
MetaTrader keeps it under %APPDATA%\\MetaQuotes\\Terminal\\<id>\\ and writes the install path into
an origin.txt file there, which is the only reliable way back from an install to its data.
"""

from __future__ import annotations

import codecs
import os
import sys
import tomllib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_ENV = "STRATEGYLAB_CONFIG"
TERMINAL_EXE = "terminal64.exe"
METAEDITOR_EXE = "MetaEditor64.exe"
STRATEGIES_SUBDIR = Path("MQL5") / "Experts" / "StrategyLab"

# Folders under bases\ that MetaTrader creates for itself rather than for a trade server.
_NON_SERVER_BASES = {"custom", "default", "signals"}

_KNOWN_KEYS: dict[str, set[str]] = {
    "terminal": {"path", "portable", "data_dir"},
    "account": {"server"},
    "workspace": {"dir"},
}


class ConfigError(Exception):
    """Something needed to work with MetaTrader is missing or wrong; the message says what to do."""


class LocalConfigError(ConfigError):
    pass


class TerminalNotFoundError(ConfigError):
    pass


class MetaEditorNotFoundError(ConfigError):
    pass


class DataFolderNotFoundError(ConfigError):
    pass


class AmbiguousTerminalError(ConfigError):
    pass


class TerminalRunningError(ConfigError):
    pass


@dataclass(frozen=True)
class TerminalInstall:
    install_dir: Path
    data_dir: Path
    portable: bool

    @property
    def terminal_exe(self) -> Path:
        return self.install_dir / TERMINAL_EXE

    @property
    def metaeditor_exe(self) -> Path:
        return self.install_dir / METAEDITOR_EXE

    @property
    def mql5_dir(self) -> Path:
        return self.data_dir / "MQL5"

    @property
    def experts_dir(self) -> Path:
        return self.mql5_dir / "Experts"

    @property
    def strategies_dir(self) -> Path:
        return self.data_dir / STRATEGIES_SUBDIR

    def history_servers(self) -> list[str]:
        """Trade servers this data folder holds history for."""
        bases = self.data_dir / "bases"
        if not bases.is_dir():
            return []
        return sorted(
            entry.name
            for entry in bases.iterdir()
            if entry.is_dir() and entry.name.lower() not in _NON_SERVER_BASES
        )


@dataclass(frozen=True)
class Candidate:
    """A MetaTrader 5 install found on disk. data_dir is None if it has never been started."""

    install_dir: Path
    data_dir: Path | None
    portable: bool


@dataclass(frozen=True)
class Settings:
    terminal: TerminalInstall
    source: str
    account_server: str | None
    workspace_dir: Path
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProcessInfo:
    pid: int
    name: str
    exe: Path | None
    cmdline: tuple[str, ...]


@dataclass(frozen=True)
class RunningTerminal:
    pid: int
    exe: Path | None
    data_dir: Path | None


def path_key(path: Path | str) -> str:
    """Comparable form of a Windows path: absolute, normalised separators, case-folded."""
    return os.path.normcase(os.path.normpath(Path(path).absolute())).rstrip("\\/")


def decode_mt_text(raw: bytes) -> str:
    """Decode a text file written by MetaTrader.

    The terminal and MetaEditor write their own text files (logs, origin.txt) as UTF-16LE with a
    BOM. User-supplied files can be anything, so fall back through UTF-8 to cp1252.
    """
    if raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return raw.decode("utf-16")
    if raw.startswith(codecs.BOM_UTF8):
        return raw[len(codecs.BOM_UTF8) :].decode("utf-8")
    if len(raw) >= 4 and raw[1] == 0 and raw[3] == 0:
        return raw.decode("utf-16-le")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


def local_config_path() -> Path:
    override = os.environ.get(CONFIG_ENV)
    return Path(override) if override else REPO_ROOT / "local.toml"


def default_appdata() -> Path | None:
    appdata = os.environ.get("APPDATA")
    return Path(appdata) if appdata else None


def default_search_roots() -> list[Path]:
    """Folders whose immediate children are checked for a MetaTrader 5 install."""
    roots: list[Path] = []
    for var in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
        value = os.environ.get(var)
        if value:
            roots.append(Path(value))
    local = os.environ.get("LOCALAPPDATA")
    if local:
        roots.append(Path(local) / "Programs")
    # Dedicated copies for backtesting are commonly kept at the drive root, e.g. C:\MT5-Lab.
    roots.append(Path(os.environ.get("SYSTEMDRIVE", "C:") + "\\"))
    unique: dict[str, Path] = {}
    for root in roots:
        unique.setdefault(path_key(root), root)
    return list(unique.values())


def origin_records(appdata: Path | None) -> list[tuple[Path, Path]]:
    """(install folder, data folder) pairs read from each data folder's origin.txt."""
    if appdata is None:
        return []
    terminals = appdata / "MetaQuotes" / "Terminal"
    if not terminals.is_dir():
        return []
    records = []
    for folder in sorted(terminals.iterdir()):
        origin = folder / "origin.txt"
        if not origin.is_file():
            continue
        try:
            install = decode_mt_text(origin.read_bytes()).strip()
        except OSError:
            continue
        if install:
            records.append((Path(install), folder))
    return records


def data_folders_by_install(appdata: Path | None) -> dict[str, Path]:
    return {path_key(install): data for install, data in origin_records(appdata)}


def discover_candidates(
    appdata: Path | None = None,
    search_roots: Sequence[Path] | None = None,
) -> list[Candidate]:
    if search_roots is None:
        search_roots = default_search_roots()
    origins = data_folders_by_install(appdata)
    install_dirs: dict[str, Path] = {}
    for install, _ in origin_records(appdata):
        if (install / TERMINAL_EXE).is_file():
            install_dirs.setdefault(path_key(install), install)
    for root in search_roots:
        try:
            children = sorted(root.iterdir())
        except OSError:
            continue
        for child in children:
            try:
                if (child / TERMINAL_EXE).is_file():
                    install_dirs.setdefault(path_key(child), child)
            except OSError:
                continue

    candidates = []
    for key, install in install_dirs.items():
        # An MQL5 folder inside the install means it has been started with /portable.
        if (install / "MQL5").is_dir():
            candidates.append(Candidate(install, install, portable=True))
        else:
            candidates.append(Candidate(install, origins.get(key), portable=False))
    return candidates


def choose_candidate(candidates: Sequence[Candidate], searched: Sequence[Path]) -> Candidate:
    usable = [c for c in candidates if c.data_dir is not None]
    if not candidates:
        where = ", ".join(str(p) for p in searched) or "nowhere"
        raise TerminalNotFoundError(
            "No MetaTrader 5 install was found (searched the MetaQuotes data folders under "
            f"%APPDATA% and {where}). Install MetaTrader 5, or copy local.toml.example to "
            "local.toml and set [terminal] path to the folder that contains terminal64.exe."
        )
    if not usable:
        installs = ", ".join(str(c.install_dir) for c in candidates)
        raise DataFolderNotFoundError(
            f"Found MetaTrader 5 at {installs}, but it has never been started, so it has no "
            "data folder yet. Start it once (with /portable if it is a dedicated copy), log in "
            "to a demo account, and close it."
        )
    portable = [c for c in usable if c.portable]
    if len(portable) == 1:
        return portable[0]
    if len(usable) == 1:
        return usable[0]
    listed = "\n".join(
        f"  {c.install_dir} ({'portable' if c.portable else 'data folder ' + str(c.data_dir)})"
        for c in usable
    )
    raise AmbiguousTerminalError(
        f"Found {len(usable)} MetaTrader 5 installs and cannot tell which one to use:\n{listed}\n"
        "Copy local.toml.example to local.toml and set [terminal] path to the one dedicated "
        "to backtesting."
    )


def read_local_config(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise LocalConfigError(
            f"{path} is not valid TOML: {exc}. Windows paths need single quotes, for example "
            "path = 'C:\\MT5-Lab'."
        ) from exc
    except OSError as exc:
        raise LocalConfigError(f"Cannot read {path}: {exc}.") from exc

    for section, value in data.items():
        if section not in _KNOWN_KEYS:
            known = ", ".join(f"[{name}]" for name in _KNOWN_KEYS)
            raise LocalConfigError(f"{path}: unknown section [{section}]. Known sections: {known}.")
        if not isinstance(value, dict):
            raise LocalConfigError(f"{path}: '{section}' must be a [{section}] table.")
        for key in value:
            if key not in _KNOWN_KEYS[section]:
                known = ", ".join(sorted(_KNOWN_KEYS[section]))
                raise LocalConfigError(
                    f"{path}: unknown key '{key}' in [{section}]. Known keys: {known}."
                )
    return data


def _typed(table: Mapping[str, Any], key: str, kind: type, where: str) -> Any:
    value = table.get(key)
    if value is not None and not isinstance(value, kind):
        raise LocalConfigError(f"{where}: '{key}' must be a {kind.__name__}, got {value!r}.")
    return value


def install_from_path(
    configured: Path,
    portable: bool | None,
    data_dir: Path | None,
    appdata: Path | None,
    where: str,
) -> TerminalInstall:
    install = configured.parent if configured.suffix.lower() == ".exe" else configured
    if not install.exists():
        raise TerminalNotFoundError(
            f"{where} sets [terminal] path to {configured}, which does not exist. Fix the path, "
            "or remove it to let StrategyLab search for an install."
        )
    if not (install / TERMINAL_EXE).is_file():
        raise TerminalNotFoundError(
            f"{install} does not contain {TERMINAL_EXE}. Point [terminal] path in {where} at "
            "the folder MetaTrader 5 is installed in."
        )
    if not (install / METAEDITOR_EXE).is_file():
        raise MetaEditorNotFoundError(
            f"{install} has {TERMINAL_EXE} but no {METAEDITOR_EXE}, which is needed to compile "
            "strategies. It ships with every MetaTrader 5 install; repair or reinstall it."
        )

    if portable is None:
        portable = (install / "MQL5").is_dir()

    if portable:
        resolved = install
        if not (install / "MQL5").is_dir():
            raise DataFolderNotFoundError(
                f"{install} is set up as portable but has no MQL5 folder yet. Start it once with "
                f'"{install / TERMINAL_EXE}" /portable, log in to a demo account, then close it.'
            )
    elif data_dir is not None:
        resolved = data_dir
        if not (data_dir / "MQL5").is_dir():
            raise DataFolderNotFoundError(
                f"{where} sets [terminal] data_dir to {data_dir}, which has no MQL5 folder. Use "
                "File > Open Data Folder in the terminal to find the right one."
            )
    else:
        found = data_folders_by_install(appdata).get(path_key(install))
        if found is None or not (found / "MQL5").is_dir():
            raise DataFolderNotFoundError(
                f"No data folder under %APPDATA%\\MetaQuotes\\Terminal belongs to {install}. "
                "Start the terminal once so it creates one, or set [terminal] data_dir in "
                f"{where}. For a dedicated copy, set portable = true instead."
            )
        resolved = found
    return TerminalInstall(install_dir=install, data_dir=resolved, portable=portable)


def load_settings(
    config_path: Path | None = None,
    *,
    appdata: Path | None = None,
    search_roots: Sequence[Path] | None = None,
) -> Settings:
    if sys.platform != "win32":
        raise ConfigError(
            "StrategyLab drives MetaTrader 5 through its Windows executables, so it only runs "
            "on Windows."
        )
    if appdata is None:
        appdata = default_appdata()
    if config_path is None:
        config_path = local_config_path()

    data: dict[str, Any] = {}
    source = "auto-discovery (no local.toml)"
    base_dir = REPO_ROOT
    if config_path.exists():
        data = read_local_config(config_path)
        source = str(config_path)
        base_dir = config_path.parent

    terminal = data.get("terminal", {})
    account = data.get("account", {})
    workspace = data.get("workspace", {})
    where = config_path.name

    configured_path = _typed(terminal, "path", str, where)
    portable = _typed(terminal, "portable", bool, where)
    data_dir = _typed(terminal, "data_dir", str, where)
    server = _typed(account, "server", str, where)
    workspace_dir = Path(_typed(workspace, "dir", str, where) or "workspace")
    if not workspace_dir.is_absolute():
        workspace_dir = base_dir / workspace_dir

    notes: list[str] = []
    if configured_path:
        install = install_from_path(
            Path(configured_path),
            portable,
            Path(data_dir) if data_dir else None,
            appdata,
            where,
        )
    else:
        roots = list(search_roots) if search_roots is not None else default_search_roots()
        chosen = choose_candidate(discover_candidates(appdata, roots), roots)
        install = install_from_path(chosen.install_dir, chosen.portable, None, appdata, where)
        if config_path.exists():
            source = f"{config_path} (terminal auto-discovered)"

    if not install.portable:
        notes.append(
            "This terminal is not a portable copy. If it is also the terminal you trade from, "
            "backtests will compete with it for the data folder; a dedicated copy started with "
            "/portable avoids that."
        )
    if server and server not in install.history_servers():
        notes.append(
            f"No history has been downloaded for server '{server}' in this data folder yet. Log "
            "the terminal in to that server once and open a chart."
        )

    return Settings(
        terminal=install,
        source=source,
        account_server=server or None,
        workspace_dir=workspace_dir,
        notes=tuple(notes),
    )


def process_snapshot() -> list[ProcessInfo]:
    import psutil

    snapshot = []
    for proc in psutil.process_iter(["pid", "name"]):
        name = proc.info.get("name") or ""
        if name.lower() != TERMINAL_EXE:
            continue
        try:
            exe: Path | None = Path(proc.exe())
        except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
            exe = None
        try:
            cmdline = tuple(proc.cmdline())
        except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
            cmdline = ()
        snapshot.append(ProcessInfo(pid=proc.info["pid"], name=name, exe=exe, cmdline=cmdline))
    return snapshot


def running_terminals(
    processes: Iterable[ProcessInfo] | None = None,
    appdata: Path | None = None,
) -> list[RunningTerminal]:
    if processes is None:
        processes = process_snapshot()
    if appdata is None:
        appdata = default_appdata()
    origins = data_folders_by_install(appdata)
    running = []
    for proc in processes:
        if proc.name.lower() != TERMINAL_EXE:
            continue
        data_dir: Path | None = None
        if proc.exe is not None:
            if any(arg.lower() == "/portable" for arg in proc.cmdline[1:]):
                data_dir = proc.exe.parent
            else:
                data_dir = origins.get(path_key(proc.exe.parent))
        running.append(RunningTerminal(pid=proc.pid, exe=proc.exe, data_dir=data_dir))
    return running


def terminals_using(
    install: TerminalInstall,
    processes: Iterable[ProcessInfo] | None = None,
    appdata: Path | None = None,
) -> list[RunningTerminal]:
    """Running terminals that use this install's data folder.

    A process whose data folder cannot be worked out counts as a match when it runs this
    install's terminal64.exe: wrongly refusing to launch is cheap, two terminals on one data
    folder is not.
    """
    target = path_key(install.data_dir)
    exe = path_key(install.terminal_exe)
    matches = []
    for running in running_terminals(processes, appdata):
        if running.data_dir is not None:
            if path_key(running.data_dir) == target:
                matches.append(running)
        elif running.exe is not None and path_key(running.exe) == exe:
            matches.append(running)
    return matches


def ensure_terminal_idle(
    install: TerminalInstall,
    processes: Iterable[ProcessInfo] | None = None,
    appdata: Path | None = None,
) -> None:
    busy = terminals_using(install, processes, appdata)
    if busy:
        pids = ", ".join(str(r.pid) for r in busy)
        raise TerminalRunningError(
            f"MetaTrader 5 is already running against {install.data_dir} (PID {pids}). Close "
            "it and try again: only one terminal can use a data folder at a time, and "
            "StrategyLab starts and stops this one itself."
        )

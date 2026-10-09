"""Show what StrategyLab found out about MetaTrader 5, then compile a strategy as a smoke test.

    python scripts/doctor.py                  compile the shipped "Moving Average" example
    python scripts/doctor.py path/to/ea.mq5   compile something else (.mq5, .ex5 or .zip)
    python scripts/doctor.py --force ...      compile again even if a cached result exists

Exit status: 0 all good, 1 the strategy failed to compile, 2 MetaTrader could not be found or
used, 3 the upload was rejected or MetaEditor could not run.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from _venv import ensure_venv

ensure_venv()

try:
    from strategylab.compiler import CompileError, CompileResult, compile_upload
    from strategylab.config import (
        ConfigError,
        Settings,
        default_appdata,
        default_search_roots,
        discover_candidates,
        load_settings,
        path_key,
        process_snapshot,
        running_terminals,
        terminals_using,
    )
except ImportError:
    sys.exit("StrategyLab is not installed. Run `python tasks.py setup` first.")

LABEL_WIDTH = 13


def line(label: str, value: object) -> None:
    print(f"  {label:<{LABEL_WIDTH}}{value}")


def plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def report_settings(settings: Settings) -> None:
    terminal = settings.terminal
    mode = "portable" if terminal.portable else "per-user data folder"
    print("MetaTrader 5")
    line("settings", settings.source)
    line("terminal", terminal.terminal_exe)
    line("MetaEditor", terminal.metaeditor_exe)
    line("data folder", f"{terminal.data_dir} ({mode})")
    line("server", settings.account_server or "not set in local.toml")
    servers = terminal.history_servers()
    line("history for", ", ".join(servers) if servers else "no trade server yet")
    line("workspace", settings.workspace_dir)

    processes = process_snapshot()
    running = running_terminals(processes)
    mine = terminals_using(terminal, processes)
    if mine:
        pids = ", ".join(str(r.pid) for r in mine)
        line("running", f"yes, PID {pids}. Close it before running backtests.")
    else:
        line("running", "no")
    for note in settings.notes:
        line("note", note)

    others = [
        c
        for c in discover_candidates(default_appdata(), default_search_roots())
        if path_key(c.install_dir) != path_key(terminal.install_dir)
    ]
    if others:
        print("\nOther MetaTrader 5 installs on this machine (not used)")
        for candidate in others:
            pids = [
                str(r.pid)
                for r in running
                if r.exe is not None and path_key(r.exe.parent) == path_key(candidate.install_dir)
            ]
            state = f", running as PID {', '.join(pids)}" if pids else ""
            data = candidate.data_dir or "never started"
            print(f"  {candidate.install_dir}  (data: {data}{state})")


def report_compile(result: CompileResult, settings: Settings) -> None:
    staged = result.strategy
    terminal = settings.terminal
    line("staged in", staged.folder)
    if staged.notes:
        for note in staged.notes:
            line("note", note)
    if not result.compiled:
        line("result", "OK, prebuilt .ex5 (nothing to compile)" if result.ok else "FAILED")
        if result.ok:
            line("expert", staged.expert_path(terminal))
        return
    status = "OK" if result.ok else "FAILED"
    counts = f"{plural(len(result.errors), 'error')}, {plural(len(result.warnings), 'warning')}"
    timing = f", {result.elapsed_ms} ms" if result.elapsed_ms is not None else ""
    source = "cached result" if result.cached else "compiled now"
    line("result", f"{status}, {counts}{timing} ({source})")
    if result.ok:
        line("expert", staged.expert_path(terminal))
    if result.log_path:
        line("log", result.log_path)
    for note in result.notes:
        if note not in staged.notes:
            line("note", note)
    for diagnostic in result.diagnostics:
        where = diagnostic.file or "?"
        if diagnostic.line is not None:
            where += f":{diagnostic.line}:{diagnostic.column}"
        code = f" {diagnostic.code}" if diagnostic.code is not None else ""
        print(f"    {where}  {diagnostic.severity}{code}  {diagnostic.message}")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("strategy", nargs="?", type=Path, help=".mq5, .ex5 or .zip to compile")
    parser.add_argument("--force", action="store_true", help="ignore any cached compile result")
    args = parser.parse_args(argv)

    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"MetaTrader 5 could not be set up:\n  {exc}")
        return 2
    report_settings(settings)

    target: Path = args.strategy or (
        settings.terminal.experts_dir / "Examples" / "Moving Average" / "Moving Average.mq5"
    )
    print(f"\nCompiling {target}")
    if not target.is_file():
        print("  not found. The terminal installs its examples on first start; start it once.")
        return 3
    try:
        result = compile_upload(target, settings.terminal, force=args.force)
    except CompileError as exc:
        print(f"  {exc}")
        return 3
    report_compile(result, settings)
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

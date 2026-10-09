"""Project task runner. Standard library only, so it works before anything is installed.

python tasks.py setup     create .venv (Python 3.11+) and install backend and frontend deps
python tasks.py lint      ruff, eslint and the TypeScript compiler
python tasks.py fmt       apply ruff fixes and formatting
python tasks.py test      pytest (extra arguments are passed through)
python tasks.py dev       backend and frontend dev servers together, Ctrl+C stops both
python tasks.py build     production build of the frontend
python tasks.py doctor    show what was discovered and compile a sample EA
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
VENV = ROOT / ".venv"
WINDOWS = os.name == "nt"
VENV_PYTHON = VENV / ("Scripts/python.exe" if WINDOWS else "bin/python")
BACKEND_PORT = 8000

TASKS: dict[str, Callable[[list[str]], int]] = {}


def task(fn: Callable[[list[str]], int]) -> Callable[[list[str]], int]:
    TASKS[fn.__name__] = fn
    return fn


def run(cmd: Sequence[str | Path], cwd: Path = ROOT) -> int:
    printable = " ".join(str(part) for part in cmd)
    print(f"\n> {printable}", flush=True)
    return subprocess.call([str(part) for part in cmd], cwd=cwd)


def run_all(*commands: tuple[Sequence[str | Path], Path]) -> int:
    """Run every command even after a failure, so one lint pass reports everything."""
    codes = [run(cmd, cwd) for cmd, cwd in commands]
    return next((code for code in codes if code), 0)


def npm() -> str:
    found = shutil.which("npm")
    if not found:
        sys.exit("npm was not found on PATH. Install Node.js 20 or newer.")
    return found


def venv_python() -> Path:
    if not VENV_PYTHON.exists():
        sys.exit("No .venv found. Run `python tasks.py setup` first.")
    return VENV_PYTHON


def frontend_ready() -> None:
    if not (FRONTEND / "node_modules").is_dir():
        sys.exit("frontend/node_modules is missing. Run `python tasks.py setup` first.")


def find_python311() -> list[str] | None:
    if sys.version_info >= (3, 11):
        return [sys.executable]
    if WINDOWS and shutil.which("py"):
        probe = subprocess.run(["py", "-3.11", "-c", "import sys"], capture_output=True)
        if probe.returncode == 0:
            return ["py", "-3.11"]
    for name in ("python3.11", "python3.12", "python3.13"):
        found = shutil.which(name)
        if found:
            return [found]
    return None


@task
def setup(args: list[str]) -> int:
    uv = shutil.which("uv")
    if not VENV_PYTHON.exists():
        interpreter = find_python311()
        if interpreter:
            code = run([*interpreter, "-m", "venv", VENV])
        elif uv:
            code = run([uv, "venv", VENV, "--python", "3.11"])
        else:
            print("Python 3.11 or newer is required. Install it, or install uv, then rerun.")
            return 1
        if code:
            return code
    # A venv made by uv has no pip, so prefer uv for installing whenever it is available.
    if uv:
        install = [uv, "pip", "install", "--python", VENV_PYTHON]
    else:
        install = [VENV_PYTHON, "-m", "pip", "install"]
    code = run([*install, "-e", f"{BACKEND}[dev]"])
    if code:
        return code
    lockfile = FRONTEND / "package-lock.json"
    return run([npm(), "ci" if lockfile.exists() else "install"], cwd=FRONTEND)


@task
def lint(args: list[str]) -> int:
    python = venv_python()
    frontend_ready()
    return run_all(
        ([python, "-m", "ruff", "check", "."], ROOT),
        ([python, "-m", "ruff", "format", "--check", "."], ROOT),
        ([npm(), "run", "lint"], FRONTEND),
        ([npm(), "run", "typecheck"], FRONTEND),
    )


@task
def fmt(args: list[str]) -> int:
    python = venv_python()
    return run_all(
        ([python, "-m", "ruff", "check", "--fix", "."], ROOT),
        ([python, "-m", "ruff", "format", "."], ROOT),
    )


@task
def test(args: list[str]) -> int:
    return run([venv_python(), "-m", "pytest", *args])


@task
def build(args: list[str]) -> int:
    frontend_ready()
    return run([npm(), "run", "build"], cwd=FRONTEND)


@task
def doctor(args: list[str]) -> int:
    return run([venv_python(), ROOT / "scripts" / "doctor.py", *args])


def stop_tree(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    if WINDOWS:
        # npm and uvicorn's reloader both spawn children; only a tree kill gets all of them.
        subprocess.call(
            ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        os.killpg(proc.pid, signal.SIGTERM)
    proc.wait()


@task
def dev(args: list[str]) -> int:
    python = venv_python()
    frontend_ready()
    backend_cmd = [
        str(python),
        "-m",
        "uvicorn",
        "strategylab.api:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(BACKEND_PORT),
        "--reload",
        "--reload-dir",
        str(BACKEND / "strategylab"),
    ]
    procs = [
        subprocess.Popen(backend_cmd, cwd=ROOT, start_new_session=not WINDOWS),
        subprocess.Popen([npm(), "run", "dev"], cwd=FRONTEND, start_new_session=not WINDOWS),
    ]
    print(f"\nbackend  http://127.0.0.1:{BACKEND_PORT}\nfrontend http://127.0.0.1:5173\n")
    try:
        while all(proc.poll() is None for proc in procs):
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        for proc in procs:
            stop_tree(proc)
    return 0


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in TASKS:
        print(__doc__)
        return 0 if not argv else 2
    return TASKS[argv[0]](argv[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

"""Lets the scripts be started with any Python: they rerun themselves under the project venv."""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
VENV = REPO_ROOT / ".venv"


def ensure_venv() -> None:
    python = VENV / "Scripts" / "python.exe"
    if not python.exists() or Path(sys.prefix).resolve() == VENV.resolve():
        return
    raise SystemExit(subprocess.call([str(python), *sys.argv]))

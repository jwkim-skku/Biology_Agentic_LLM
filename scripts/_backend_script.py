from __future__ import annotations

import runpy
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"


def run_backend_script(name: str) -> None:
    """Run a backend script while preserving its own __file__ path."""
    sys.path.insert(0, str(BACKEND))
    runpy.run_path(str(BACKEND / "scripts" / name), run_name="__main__")

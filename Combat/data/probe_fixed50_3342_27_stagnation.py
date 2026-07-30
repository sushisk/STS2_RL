from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
script = ROOT / "Combat" / "data" / "probe_fixed50_partial_case.py"
raise SystemExit(
    subprocess.call([sys.executable, str(script), "--case", "fixed50:3342-27"])
)

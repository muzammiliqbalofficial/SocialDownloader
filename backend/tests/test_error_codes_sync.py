"""Fails when the generated frontend error catalog drifts from the Python one."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
GENERATOR = BACKEND_ROOT / "scripts" / "gen_error_codes.py"
GENERATED = BACKEND_ROOT.parent / "frontend" / "lib" / "errors.ts"


def test_generated_typescript_is_current():
    result = subprocess.run(
        [sys.executable, str(GENERATOR), "--check"],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        "frontend/lib/errors.ts is stale. Run: "
        "python backend/scripts/gen_error_codes.py\n" + result.stdout + result.stderr
    )


def test_generated_file_contains_every_code():
    from app.core.errors import ErrorCode

    content = GENERATED.read_text(encoding="utf-8")
    for code in ErrorCode:
        assert f'"{code}"' in content

"""Parse untrusted docs in a killable subprocess — malicious files can't hang or exploit the main process."""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path

log = logging.getLogger("monogram.ingestion.sandbox")


def _unlink(p: Path) -> None:
    try:
        p.unlink()
    except OSError:
        pass


def run_parser(kind: str, input_path: str, timeout: float = 60.0) -> str | None:
    fd, out_name = tempfile.mkstemp(suffix=".out", prefix="monogram-parse-")
    os.close(fd)
    out = Path(out_name)
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "monogram._parse_worker", kind, input_path, out_name],
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        log.warning("sandbox: %s parse timed out after %.0fs; killed", kind, timeout)
        _unlink(out)
        return None
    if proc.returncode != 0:
        log.warning(
            "sandbox: %s parse failed (rc=%s): %s",
            kind, proc.returncode, proc.stderr.decode("utf-8", "replace")[:200],
        )
        _unlink(out)
        return None
    try:
        return out.read_text(encoding="utf-8") or None
    except OSError:
        return None
    finally:
        _unlink(out)

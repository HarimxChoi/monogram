"""HWP (Korean word-processor) extractor — pyhwp (hwp5txt CLI).

Previously used LibreOffice headless conversion. That wouldn't fit on
the default e2-micro VM: ~300MB install, upstream CVE chase, and snap
confinement blocks it in systemd service context. Switched to pyhwp:
pure Python, ~10MB install, no external binaries, far narrower attack
surface.

Covers HWP5 — the dominant Korean document format in the wild. HWPX
(ZIP + XML container) is NOT supported by pyhwp; HWPX drops return a
clear warning so the attachment lands in the vault with a note.

Security posture:
  - pyhwp is pure Python. No URL handlers, no macro engine, no filesystem
    side-effects, no env-var expansion. The LibreOffice CVE classes
    (CVE-2024-12425 / 12426, CVE-2025-1080, CVE-2018-16858) do not apply.
  - Worst case is a parser bug: malformed HWP crashing or hanging the
    subprocess. Bounded by:
      * minimal env (no user secrets inherited)
      * 60s hard timeout
      * 20MB input size cap
      * dedicated temp dir per invocation
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from .base import ExtractionResult

log = logging.getLogger("monogram.ingestion.hwp")


_SUBPROCESS_TIMEOUT_SECONDS = 60
_MAX_INPUT_BYTES = 20 * 1024 * 1024  # 20 MB

# HWPX is ZIP + XML (starts with PK\x03\x04). HWP5 is an OLE Compound
# Document (magic D0 CF 11 E0 A1 B1 1A E1).
_HWPX_MAGIC = b"PK\x03\x04"


async def extract_from_bytes(
    data: bytes, filename: str = "document.hwp"
) -> ExtractionResult:
    """Extract plain text from HWP5 bytes via pyhwp's `hwp5txt`."""
    if len(data) > _MAX_INPUT_BYTES:
        return ExtractionResult(
            source_type="hwp",
            url="",
            text=f"[HWP file too large: {len(data)} bytes > {_MAX_INPUT_BYTES}]",
            success=False,
            extraction_method="size_cap_exceeded",
            warning=f"size_cap_{_MAX_INPUT_BYTES}",
        )

    if data.startswith(_HWPX_MAGIC) or filename.lower().endswith(".hwpx"):
        return ExtractionResult(
            source_type="hwp",
            url="",
            text=(
                f"[HWPX format not supported by pyhwp. File preserved as "
                f"attachment: {filename}. Convert to HWP5 or PDF for body "
                f"extraction.]"
            ),
            success=False,
            extraction_method="hwpx_unsupported",
            warning="hwpx_not_supported",
        )

    hwp5txt_bin = shutil.which("hwp5txt")
    if not hwp5txt_bin:
        return ExtractionResult(
            source_type="hwp",
            url="",
            text=(
                "[pyhwp not installed — "
                "`pip install 'mono-gram[ingestion-office]'`]"
            ),
            success=False,
            extraction_method="pyhwp_missing",
            warning="pyhwp_not_found",
        )

    text = await _hwp5txt_extract(data, hwp5txt_bin)
    if not text:
        return ExtractionResult(
            source_type="hwp",
            url="",
            text=f"[HWP extraction failed for {filename}]",
            success=False,
            extraction_method="extraction_failed",
            warning="pyhwp_extraction_error",
        )

    return ExtractionResult(
        source_type="hwp",
        url="",
        text=text,
        metadata={"filename": filename, "extractor": "pyhwp"},
        extraction_method="pyhwp",
    )


async def _hwp5txt_extract(data: bytes, bin_path: str) -> str | None:
    """Run `hwp5txt <path>` in an isolated subprocess, return stdout."""
    def _sync() -> str | None:
        with tempfile.TemporaryDirectory(prefix="monogram-hwp-") as workdir:
            input_path = Path(workdir) / "in.hwp"
            input_path.write_bytes(data)

            try:
                result = subprocess.run(
                    [bin_path, str(input_path)],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=_SUBPROCESS_TIMEOUT_SECONDS,
                    env=_minimal_env(Path(workdir)),
                    check=False,
                )
            except subprocess.TimeoutExpired:
                log.warning(
                    "hwp: hwp5txt timed out after %ds",
                    _SUBPROCESS_TIMEOUT_SECONDS,
                )
                return None
            except OSError as e:
                log.warning("hwp: hwp5txt subprocess error: %s", e)
                return None

            if result.returncode != 0:
                log.warning(
                    "hwp: hwp5txt exit %d: %s",
                    result.returncode, result.stderr[:200],
                )
                return None
            return result.stdout or None

    return await asyncio.to_thread(_sync)


def _minimal_env(home: Path) -> dict[str, str]:
    """Strip parent env for the hwp5txt subprocess.

    pyhwp doesn't expand env vars the way LibreOffice did, but keeping
    the minimal-env discipline costs nothing and forecloses future
    surprise if pyhwp ever adds config-file lookup.
    """
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/local/bin"),
        "LANG": os.environ.get("LANG", "en_US.UTF-8"),
        "LC_ALL": os.environ.get("LC_ALL", "en_US.UTF-8"),
        "HOME": str(home),
    }

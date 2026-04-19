"""HWP (Korean word-processor) extractor — LibreOffice → PDF → pdf.py.

This path has real threat surface. The 2024-12425 / 12426 and 2025-1080
CVEs hit headless LibreOffice specifically:

  CVE-2024-12425: path traversal via embedded font names → arbitrary
                  file write in the filesystem
  CVE-2024-12426: vnd.sun.star.expand URI → exfiltrates env vars and
                  INI-file values
  CVE-2025-1080:  vnd.libreoffice.command URI → internal macro execution
                  with attacker-controlled args
  CVE-2018-16858: mouse-over event handlers → directory traversal → RCE

All of the above trigger on DOCUMENT LOAD, which happens in our
conversion path. Fixes are in LibreOffice 25.2.1+ (and backports).

Mitigations implemented here:
  1. Version check — refuse if LibreOffice < 25.2.1
  2. Isolated subprocess — minimal env (only PATH + dedicated HOME),
     no user environment inherited (blocks CVE-2024-12426 env leakage)
  3. Dedicated temp profile dir — LibreOffice writes lots of state to
     ~/.config/libreoffice; giving it a fresh temp dir keeps the attack
     blast radius contained
  4. Hard subprocess timeout (60s)
  5. Input size cap (20MB — HWP files rarely exceed this)
  6. --safe-mode flag (disables extensions, macros, custom configs)
  7. --headless --norestore --nofirststartwizard — minimum feature surface

NOT mitigated (user must understand):
  - Sandbox escape inside LibreOffice itself (relies on LibreOffice
    internal security). Best additional layer is running monogram in a
    container or as a non-privileged user.
  - HWP-specific parser bugs (HWP is less-audited than OOXML).

Docs: docs/security-hwp.md covers this in detail (to be created).
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .base import ExtractionResult

log = logging.getLogger("monogram.ingestion.hwp")


_MIN_LIBREOFFICE = (25, 2, 1)
_SUBPROCESS_TIMEOUT_SECONDS = 60
_MAX_INPUT_BYTES = 20 * 1024 * 1024  # 20 MB


async def extract_from_bytes(
    data: bytes, filename: str = "document.hwp"
) -> ExtractionResult:
    """Convert HWP bytes → PDF via LibreOffice → markdown via pdf.py."""
    if len(data) > _MAX_INPUT_BYTES:
        return ExtractionResult(
            source_type="hwp",
            url="",
            text=f"[HWP file too large: {len(data)} bytes > {_MAX_INPUT_BYTES}]",
            success=False,
            extraction_method="size_cap_exceeded",
            warning=f"size_cap_{_MAX_INPUT_BYTES}",
        )

    libreoffice_bin = shutil.which("libreoffice") or shutil.which("soffice")
    if not libreoffice_bin:
        return ExtractionResult(
            source_type="hwp",
            url="",
            text="[LibreOffice not installed — required for HWP extraction]",
            success=False,
            extraction_method="libreoffice_missing",
            warning="libreoffice_not_found",
        )

    version = await _libreoffice_version(libreoffice_bin)
    if version is None:
        return ExtractionResult(
            source_type="hwp",
            url="",
            text="[LibreOffice version check failed]",
            success=False,
            extraction_method="version_check_failed",
            warning="version_unknown",
        )
    if version < _MIN_LIBREOFFICE:
        version_str = ".".join(map(str, version))
        min_str = ".".join(map(str, _MIN_LIBREOFFICE))
        return ExtractionResult(
            source_type="hwp",
            url="",
            text=(
                f"[LibreOffice {version_str} is below required minimum "
                f"{min_str}. CVE-2024-12425/12426 + CVE-2025-1080 apply. "
                f"Upgrade via your OS package manager.]"
            ),
            success=False,
            extraction_method="libreoffice_too_old",
            warning=f"unpatched_libreoffice_{version_str}",
        )

    pdf_bytes = await _convert_to_pdf(data, filename, libreoffice_bin)
    if not pdf_bytes:
        return ExtractionResult(
            source_type="hwp",
            url="",
            text=f"[HWP → PDF conversion failed for {filename}]",
            success=False,
            extraction_method="conversion_failed",
            warning="libreoffice_conversion_error",
        )

    # Delegate to pdf.py — inherits MinerU/Marker fallback tier logic
    from . import pdf as pdf_module
    pdf_result = await pdf_module.extract_from_bytes(pdf_bytes, filename=filename)

    # Re-label as hwp (it came in that way, even though body was extracted via PDF path)
    return ExtractionResult(
        source_type="hwp",
        url=pdf_result.url,
        text=pdf_result.text,
        metadata={
            **pdf_result.metadata,
            "original_format": "hwp",
            "intermediate": "pdf",
            "libreoffice_version": ".".join(map(str, version)),
        },
        extraction_method=f"libreoffice_pdf_{pdf_result.extraction_method}",
        success=pdf_result.success,
        warning=pdf_result.warning,
    )


async def _libreoffice_version(bin_path: str) -> tuple[int, ...] | None:
    """Run `libreoffice --version` and parse. Tuple of ints like (25, 2, 1)."""
    def _sync() -> tuple[int, ...] | None:
        try:
            # Hardened env for version check too
            env = _minimal_env()
            result = subprocess.run(
                [bin_path, "--version"],
                capture_output=True,
                text=True,
                timeout=10,
                env=env,
                check=False,
            )
            # Output format: "LibreOffice 25.2.1.2 <hash>"
            match = re.search(r"(\d+)\.(\d+)\.(\d+)", result.stdout)
            if match:
                return tuple(int(g) for g in match.groups())
            return None
        except (OSError, subprocess.TimeoutExpired):
            return None

    return await asyncio.to_thread(_sync)


async def _convert_to_pdf(
    data: bytes, filename: str, bin_path: str
) -> bytes | None:
    """Run LibreOffice headless conversion in an isolated temp dir."""
    def _sync() -> bytes | None:
        # Separate input, output, and profile dirs so LibreOffice can't
        # walk paths into our actual workdir. Each invocation gets a
        # FRESH profile dir — blast-radius containment for CVE-2024-12426
        # env/INI read attacks (nothing sensitive in the env, nothing in
        # the INI files since profile is brand-new).
        with tempfile.TemporaryDirectory(prefix="monogram-hwp-") as workdir:
            work = Path(workdir)
            input_path = work / "in.hwp"
            output_dir = work / "out"
            profile_dir = work / "profile"
            output_dir.mkdir()
            profile_dir.mkdir()
            input_path.write_bytes(data)

            # --headless: no GUI
            # --norestore: don't attempt to recover previous session
            # --nofirststartwizard: skip welcome wizard
            # --safe-mode: disable extensions, macros, custom configs
            # -env:UserInstallation=...: isolated profile dir (critical
            #   for CVE-2024-12426 mitigation)
            # --convert-to pdf: output format
            # --outdir: where to write the PDF
            cmd = [
                bin_path,
                "--headless",
                "--norestore",
                "--nofirststartwizard",
                "--safe-mode",
                f"-env:UserInstallation=file://{profile_dir}",
                "--convert-to", "pdf",
                "--outdir", str(output_dir),
                str(input_path),
            ]

            env = _minimal_env(profile_dir)

            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    timeout=_SUBPROCESS_TIMEOUT_SECONDS,
                    env=env,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                log.warning("hwp: libreoffice conversion timed out after %ds",
                            _SUBPROCESS_TIMEOUT_SECONDS)
                return None
            except OSError as e:
                log.warning("hwp: libreoffice subprocess error: %s", e)
                return None

            if result.returncode != 0:
                log.warning("hwp: libreoffice exit %d: %s",
                            result.returncode, result.stderr[:200])
                # Some HWP files may still produce a PDF despite nonzero
                # exit — check for output file before giving up
            pdfs = list(output_dir.glob("*.pdf"))
            if not pdfs:
                return None
            return pdfs[0].read_bytes()

    return await asyncio.to_thread(_sync)


def _minimal_env(home: Path | None = None) -> dict[str, str]:
    """Minimal environment for the LibreOffice subprocess.

    Strips the parent env to prevent CVE-2024-12426 (env-var
    exfiltration via vnd.sun.star.expand). Only passes PATH, LANG,
    and HOME — and HOME points at a temp dir to avoid disclosing
    the real user's home.
    """
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/local/bin"),
        "LANG": os.environ.get("LANG", "en_US.UTF-8"),
        "LC_ALL": os.environ.get("LC_ALL", "en_US.UTF-8"),
        "HOME": str(home) if home else "/tmp",
        # Explicitly DO NOT pass: SECRETS, GITHUB_*, TELEGRAM_*, GEMINI_*,
        # ANTHROPIC_*, OPENAI_*, AWS_*, any other env — stops CVE-2024-12426
        # from reading anything useful.
    }

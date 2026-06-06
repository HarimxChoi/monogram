"""Isolated subprocess entry for untrusted-document parsing.
Run: python -m monogram._parse_worker <kind> <input> <output>  (see ingestion/_sandbox)."""
from __future__ import annotations

import sys
from pathlib import Path


def _parse(kind: str, path: str) -> str:
    if kind == "pdf":
        import pymupdf4llm  # type: ignore
        return pymupdf4llm.to_markdown(path) or ""
    if kind == "hwp":
        import rhwp  # type: ignore
        return rhwp.parse(path).extract_text() or ""
    raise ValueError(f"unknown parser kind: {kind}")


def main() -> int:
    if len(sys.argv) != 4:
        return 2
    kind, input_path, output_path = sys.argv[1], sys.argv[2], sys.argv[3]
    Path(output_path).write_text(_parse(kind, input_path), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())

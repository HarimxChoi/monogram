"""AX-1 check: src/ may not import evals/ on the production hot path.

Exceptions: an `import evals…` / `from evals… import …` statement is
allowed if it lives inside a `try: …` block whose handlers catch
ImportError (or ModuleNotFoundError, or a bare except). Those are the
graceful-degradation patterns in cli.py (optional `monogram eval`
subgroup) and bot.py (harvest fallback for approve/deny tokens).

The prior CI check used a plain `grep -E "from evals\\b|import evals\\b"`
which matched the guarded imports too and would turn the workflow red
on a freshly-landed branch. This AST walker distinguishes guarded from
unguarded imports so the production hot path (listener, pipeline,
agents) stays covered while the CLI/bot try/except branches pass.

Exits 0 when clean, 1 when a violation is found. Invoke from CI as:

    python scripts/check_no_evals_import.py src

Callable directly (no pytest / setuptools deps) so it runs before
`pip install` in the workflow.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path


def _try_lines_with_importerror(tree: ast.AST) -> set[int]:
    """Line numbers inside a Try whose handlers catch ImportError."""
    covered: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        ok = False
        for h in node.handlers:
            if h.type is None:
                ok = True
                break
            names: list[str] = []
            if isinstance(h.type, ast.Name):
                names = [h.type.id]
            elif isinstance(h.type, ast.Tuple):
                names = [e.id for e in h.type.elts if isinstance(e, ast.Name)]
            if {"ImportError", "ModuleNotFoundError"} & set(names):
                ok = True
                break
        if not ok:
            continue
        for stmt in node.body:
            for sub in ast.walk(stmt):
                if hasattr(sub, "lineno"):
                    covered.add(sub.lineno)
    return covered


def _imports_evals(node: ast.AST) -> bool:
    if isinstance(node, ast.ImportFrom):
        return bool(node.module) and node.module.split(".")[0] == "evals"
    if isinstance(node, ast.Import):
        return any(a.name.split(".")[0] == "evals" for a in node.names)
    return False


def scan(root: Path) -> list[str]:
    violations: list[str] = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        allowed = _try_lines_with_importerror(tree)
        for node in ast.walk(tree):
            if not _imports_evals(node):
                continue
            if node.lineno in allowed:
                continue
            violations.append(
                f"{path}:{node.lineno} imports evals/ on the production hot path"
            )
    return violations


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: check_no_evals_import.py <root>", file=sys.stderr)
        return 2
    root = Path(argv[1])
    if not root.is_dir():
        print(f"not a directory: {root}", file=sys.stderr)
        return 2
    violations = scan(root)
    if violations:
        print("::error::src/ may only import evals/ inside try/except ImportError (AX-1):")
        for v in violations:
            print("  " + v)
        return 1
    print("clean — AX-1 holds")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

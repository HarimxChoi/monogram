"""One-shot v0.2 migration for an already-dirtied scheduler repo.

Run once against the live scheduler repo to:
  1. Move `projects/*.md` (no prefix) → `scheduler/projects/*.md`
  2. Merge `scheduler/tasks.md` content into the matching project file, delete it
  3. Move stray top-level category folders (e.g. `fitness/`) → `wiki/<category>/`,
     stripping fabricated dates from filenames
  4. Delete `tests/pytest_*.md` pollution files
  5. Commit a `.gitignore` that blocks future test/session pollution

Idempotent — running twice on a clean repo is a no-op.

Usage:
  python scripts/migrate_v0_2.py            # dry-run
  python scripts/migrate_v0_2.py --apply    # actually do it
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from monogram import github_store  # noqa: E402
from monogram.config import load_config  # noqa: E402

FITNESS_TO_HEALTH = {"fitness": "health"}  # extend if more bad folders exist
SCHEDULER_GITIGNORE = """\
# monogram scheduler repo — block dev/test artifacts from being committed
tests/
pytest_*.md
monogram_session*
*.session
.env
__pycache__/
"""


def _fetch_tree(repo):
    """Walk the repo root-to-leaf and return list of (path, sha, type)."""
    items: list[tuple[str, str, str]] = []
    queue = [""]
    while queue:
        dir_path = queue.pop()
        for entry in repo.get_contents(dir_path or "/"):
            if entry.type == "dir":
                queue.append(entry.path)
            else:
                items.append((entry.path, entry.sha, entry.type))
    return items


def _strip_date_from_filename(name: str) -> str:
    """cheleee_workout_2023-05-15.md → cheleee-workout.md"""
    stem = name[:-3] if name.endswith(".md") else name
    stem = re.sub(r"[-_]?\d{4}-\d{2}-\d{2}[-_]?", "-", stem).strip("-_")
    stem = re.sub(r"[_\s]+", "-", stem.lower())
    return f"{stem or 'untitled'}.md"


def plan_migrations(paths: list[str]) -> list[tuple[str, str, str]]:
    """Return [(old_path, new_path, reason), ...]. Empty new_path means delete."""
    moves: list[tuple[str, str, str]] = []
    for p in paths:
        # tests/ pollution
        if p.startswith("tests/"):
            moves.append((p, "", "remove pytest pollution"))
            continue

        # projects/*.md → scheduler/projects/*.md
        if p.startswith("projects/") and p.endswith(".md"):
            moves.append(
                (p, p.replace("projects/", "scheduler/projects/", 1),
                 "missing scheduler/ prefix")
            )
            continue

        # scheduler/tasks.md → delete (generic catch-all file that shouldn't exist)
        if p == "scheduler/tasks.md":
            moves.append((p, "", "v0.2 replaces free-form tasks.md with scheduler/projects/*"))
            continue

        # Bad top-level category folders → wiki/<mapped>/
        parts = p.split("/")
        if len(parts) >= 2 and parts[0] in FITNESS_TO_HEALTH:
            new_cat = FITNESS_TO_HEALTH[parts[0]]
            new_name = _strip_date_from_filename(parts[-1])
            moves.append(
                (p, f"wiki/{new_cat}/{new_name}",
                 f"top-level {parts[0]}/ belongs under wiki/{new_cat}/")
            )
            continue

    return moves


def apply_move(repo, old_path: str, new_path: str, message: str):
    """Create new_path with old content, then delete old_path. Empty new = delete only."""
    old_file = repo.get_contents(old_path)
    old_sha = old_file.sha
    old_content = old_file.decoded_content.decode()

    if new_path:
        try:
            existing = repo.get_contents(new_path)
            # File exists — merge bodies with a separator
            merged = (
                f"{existing.decoded_content.decode().rstrip()}\n\n"
                f"<!-- migrated from {old_path} -->\n\n"
                f"{old_content.lstrip()}"
            )
            repo.update_file(new_path, message, merged, existing.sha)
        except Exception:
            repo.create_file(new_path, message, old_content)

    repo.delete_file(old_path, message, old_sha)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="actually apply changes")
    args = ap.parse_args()

    cfg = load_config()
    print(f"Repo: {cfg.github_repo}")
    repo = github_store._repo()
    items = _fetch_tree(repo)
    all_paths = [p for p, _, _ in items]
    moves = plan_migrations(all_paths)

    print(f"\nFound {len(all_paths)} files, {len(moves)} to migrate:")
    for old, new, reason in moves:
        print(f"  {old}")
        print(f"    → {new or '(DELETE)'}  — {reason}")

    # .gitignore handling
    gitignore_path = ".gitignore"
    try:
        current_gitignore = repo.get_contents(gitignore_path).decoded_content.decode()
        need_gitignore = "pytest_" not in current_gitignore
    except Exception:
        current_gitignore = ""
        need_gitignore = True

    if need_gitignore:
        print(f"\nWill create/update .gitignore (currently {len(current_gitignore)} bytes)")

    if not args.apply:
        print("\nDry run — re-run with --apply to execute.")
        return

    print("\nApplying…")
    for old, new, reason in moves:
        print(f"  {reason}: {old}")
        try:
            apply_move(repo, old, new, f"monogram v0.2 migration: {reason}")
        except Exception as e:
            print(f"    ERROR: {e}")

    if need_gitignore:
        merged_gitignore = (
            f"{current_gitignore.rstrip()}\n\n{SCHEDULER_GITIGNORE}"
            if current_gitignore else SCHEDULER_GITIGNORE
        )
        github_store.write(
            gitignore_path, merged_gitignore,
            "monogram v0.2 migration: .gitignore — block tests/session/env pollution"
        )

    print("Done.")


if __name__ == "__main__":
    main()

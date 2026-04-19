"""CaptureStore — in-memory substitute for github_store during eval.

Patches read/write/write_multi/append and replaces `_repo()` with a
FakeRepo that raises on any direct call. Loud failure > silent real
GitHub API call.

Seed state simulates pre-existing repo files for fixtures that exercise
the Verifier's contradiction detection.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from monogram import github_store as _gs

log = logging.getLogger("monogram.evals.capture")


class FakeRepo:
    """Defensive stand-in for PyGithub Repository — any method call raises.

    Patched in by the capture fixture to catch code paths that reach for
    github_store._repo() directly (mcp_reads, queue_poller). Evals don't
    exercise those, but if a future refactor inlines them into the
    pipeline we want a loud failure rather than a silent API call.
    """

    def __getattr__(self, name: str):
        def _raise(*args, **kwargs):
            raise RuntimeError(
                f"FakeRepo: eval tried to reach GitHub via _repo().{name}. "
                f"Either the pipeline has changed (now patch it in CaptureStore) "
                f"or a test doesn't belong in evals/."
            )
        return _raise


@dataclass
class CaptureStore:
    """In-memory store patched in for evals.

    - seed  : paths that exist before the pipeline runs (verifier context)
    - writes: {path → content} — latest content per path
    - appends: {path → [line, …]} — ordered list of append()s
    - reads : list of every path read this session, for assertions
    """

    seed: dict[str, str] = field(default_factory=dict)
    writes: dict[str, str] = field(default_factory=dict)
    appends: dict[str, list[str]] = field(default_factory=dict)
    reads: list[str] = field(default_factory=list)
    write_multi_calls: list[tuple[dict, str]] = field(default_factory=list)

    # ── github_store API surface ────────────────────────────────────

    def read(self, path: str) -> str:
        self.reads.append(path)
        # Writes from this session shadow seed state.
        if path in self.writes:
            return self.writes[path]
        return self.seed.get(path, "")

    def write(self, path: str, content: str, message: str = "") -> bool:
        self.writes[path] = content
        return True

    def write_multi(self, writes: dict[str, str], message: str = "") -> bool:
        """Atomic multi-file write. Records the call for assertion."""
        self.write_multi_calls.append((dict(writes), message))
        for path, content in writes.items():
            self.writes[path] = content
        return True

    def append(self, path: str, line: str, commit_msg: str = "") -> bool:
        self.appends.setdefault(path, []).append(line)
        existing = self.writes.get(path, self.seed.get(path, ""))
        self.writes[path] = existing + ("\n" if existing else "") + line
        return True

    def _repo(self) -> FakeRepo:
        return FakeRepo()

    # ── Assertion helpers ───────────────────────────────────────────

    def written(self, path: str) -> str:
        return self.writes.get(path, "")

    def has_written(self, path: str) -> bool:
        return path in self.writes

    def any_content_contains(self, needle: str, exclude_prefix: str = "") -> bool:
        """True if `needle` appears in any written content.

        `exclude_prefix` lets credential tests say "needle must not appear
        in any path NOT prefixed with life/credentials/".
        """
        for path, content in self.writes.items():
            if exclude_prefix and path.startswith(exclude_prefix):
                continue
            if needle in content:
                return True
        return False

    def paths_touched(self) -> list[str]:
        return sorted(set(self.writes) | set(self.appends))


def install(monkeypatch, store: CaptureStore) -> None:
    """Install the CaptureStore over the real github_store module.

    Pure functions (parse_metadata, build_metadata, serialize_with_metadata)
    are NOT patched — they don't touch the network and callers expect real
    behavior.
    """
    monkeypatch.setattr(_gs, "read", store.read)
    monkeypatch.setattr(_gs, "write", store.write)
    monkeypatch.setattr(_gs, "write_multi", store.write_multi)
    monkeypatch.setattr(_gs, "append", store.append)
    monkeypatch.setattr(_gs, "_repo", store._repo)
    log.info("CaptureStore installed")

"""Cassette — record/replay shim for litellm.acompletion.

The "ONNX of eval": record real LLM responses once, replay bit-exact on
every subsequent run. Eval cost drops to zero after the first --record
pass. ONNX compiles a graph for cheap re-execution; this caches LLM I/O
for the same property.

Three modes:
  replay (default) — cache hit returns recorded response; miss raises.
  auto             — cache hit replays; miss falls through to real call
                     and records the response.
  record           — always hits real LLM, overwrites cache (serial).

Per-agent routing (D1-A):
  Each agent passes `agent_tag="classifier"` etc. to llm.complete/extract.
  monogram.llm sets current_agent_tag ContextVar for the call's duration.
  This shim reads the ContextVar and routes to cassettes/<agent>.json.
  No agent tag set → routes to cassettes/_misc.json.

Determinism note:
  Even at temperature=0 most providers are not strictly deterministic.
  The cassette IS the determinism guarantee: once recorded, replay is
  bit-exact. To detect model drift, run `monogram eval drift` which
  re-records to a parallel cassette and diffs.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import litellm
from litellm import ModelResponse
from litellm.types.utils import Choices, Message, Usage

from monogram.llm import current_agent_tag

log = logging.getLogger("monogram.evals.cassette")


class CassetteMiss(RuntimeError):
    """Raised in replay mode when no recorded entry matches the call."""


@dataclass
class CassetteEntry:
    """One recorded LLM call.

    Persisted fields are chosen so that git-diff on cassettes/*.json is
    a readable change log: model version, prompt excerpt, response, and
    recording provenance are all visible. Large opaque hashes are separate.
    """
    model: str
    prompt_hash: str
    prompt_sample: str
    temperature: float
    schema_hash: str | None
    response_content: str
    usage: dict | None = None

    # Review A1 additions — provenance and cost data for the cassette file.
    # These survive across cassette diffs: git log shows not only WHAT a
    # response was but WHEN and on WHAT MODEL it was recorded.
    latency_ms: int | None = None
    recorded_at: str = ""
    recorded_on_provider: str = ""


class Cassette:
    """Record-replay shim installed via pytest session fixture.

    Per-agent cassette files live under `cassettes/<agent>.json`. Each
    file is a dict {prompt_hash → CassetteEntry}. Agent is determined
    by monogram.llm.current_agent_tag at call time.
    """

    def __init__(self, root_dir: Path, mode: str = "replay"):
        assert mode in ("replay", "record", "auto"), f"bad mode: {mode}"
        self.root_dir = Path(root_dir)
        self.mode = mode
        self._original: Any = None
        # Map agent → {prompt_hash → CassetteEntry}
        self._files: dict[str, dict[str, CassetteEntry]] = {}
        # Track which agents had new entries written this session
        self._dirty: set[str] = set()

    # ── I/O ─────────────────────────────────────────────────────────

    def _path(self, agent: str) -> Path:
        return self.root_dir / f"{agent}.json"

    def _load(self, agent: str) -> dict[str, CassetteEntry]:
        if agent in self._files:
            return self._files[agent]
        path = self._path(agent)
        if not path.exists():
            self._files[agent] = {}
            return self._files[agent]
        try:
            raw = json.loads(path.read_text())
            self._files[agent] = {
                k: CassetteEntry(**v) for k, v in raw.items()
            }
        except Exception as e:
            log.warning("cassette: failed to load %s, starting empty: %s", path, e)
            self._files[agent] = {}
        return self._files[agent]

    def save(self) -> None:
        """Persist all dirty cassettes. Idempotent."""
        self.root_dir.mkdir(parents=True, exist_ok=True)
        for agent in self._dirty:
            path = self._path(agent)
            entries = self._files.get(agent, {})
            serializable = {k: asdict(v) for k, v in entries.items()}
            path.write_text(
                json.dumps(serializable, indent=2, sort_keys=True, ensure_ascii=False)
                + "\n"
            )
            log.info("cassette: saved %s (%d entries)", path, len(entries))
        self._dirty.clear()

    # ── Key derivation ──────────────────────────────────────────────

    @staticmethod
    def _key(kwargs: dict) -> str:
        """Canonical key: SHA-256 first 16 hex of (model, messages, temp, schema)."""
        canonical = {
            "model": kwargs.get("model"),
            "messages": kwargs.get("messages"),
            "temperature": round(float(kwargs.get("temperature", 0.0) or 0.0), 3),
            "response_format": Cassette._schema_repr(kwargs.get("response_format")),
        }
        blob = json.dumps(canonical, sort_keys=True, default=str).encode("utf-8")
        return hashlib.sha256(blob).hexdigest()[:16]

    @staticmethod
    def _schema_repr(fmt: Any) -> str | None:
        if fmt is None:
            return None
        if isinstance(fmt, dict):
            return json.dumps(fmt, sort_keys=True)
        # Pydantic model class
        try:
            return json.dumps(fmt.model_json_schema(), sort_keys=True)
        except Exception:
            return str(fmt)

    @staticmethod
    def _sample(messages: list | None) -> str:
        if not messages:
            return ""
        last = messages[-1]
        content = last.get("content") if isinstance(last, dict) else ""
        if isinstance(content, list):  # vision-style multi-part
            content = next(
                (p.get("text", "") for p in content if p.get("type") == "text"),
                "",
            )
        return (content or "")[:200]

    @staticmethod
    def _usage_dict(response) -> dict | None:
        usage = getattr(response, "usage", None)
        if usage is None:
            return None
        return {
            "prompt_tokens": getattr(usage, "prompt_tokens", None),
            "completion_tokens": getattr(usage, "completion_tokens", None),
            "total_tokens": getattr(usage, "total_tokens", None),
        }

    @staticmethod
    def _provider_of(model: str) -> str:
        return model.split("/", 1)[0] if "/" in model else model

    # ── Fake response (uses litellm.ModelResponse — review fix #1) ──

    @staticmethod
    def _fake_response(entry: CassetteEntry) -> ModelResponse:
        """Reconstruct a litellm.ModelResponse from a cassette entry.

        Uses the real ModelResponse/Choices/Message/Usage classes so future
        code that reads .id, .created, .finish_reason, .tool_calls, etc.
        doesn't break on a duck-typed stand-in.
        """
        msg = Message(role="assistant", content=entry.response_content)
        choice = Choices(index=0, finish_reason="stop", message=msg)
        usage = Usage(**entry.usage) if entry.usage else None
        return ModelResponse(
            id=f"cassette-{entry.prompt_hash}",
            created=0,
            model=entry.model,
            object="chat.completion",
            choices=[choice],
            usage=usage,
        )

    # ── The shim itself ─────────────────────────────────────────────

    async def _shim(self, **kwargs) -> ModelResponse:
        agent = current_agent_tag.get() or "_misc"
        entries = self._load(agent)
        key = self._key(kwargs)
        hit = entries.get(key)

        if self.mode == "replay":
            if hit is None:
                # Backfill escape hatch. The initial cassette landing is
                # rate-limited by the LLM provider's daily quota (Gemini
                # free tier = 20 req/day/model), so recording all 50
                # seed fixtures is a multi-day operation. While the
                # backfill is in progress, CI sets MONOGRAM_EVAL_MISS_SKIP=1
                # so missing cassettes skip instead of failing — this
                # keeps the replay-tests job green without committing
                # synthetic responses. Flip the env var off once the
                # backfill completes to restore strict coverage.
                if os.environ.get("MONOGRAM_EVAL_MISS_SKIP") == "1":
                    try:
                        import pytest
                        pytest.skip(
                            f"cassette miss (backfill pending) for "
                            f"agent={agent} model={kwargs.get('model')} "
                            f"key={key}"
                        )
                    except ImportError:
                        pass
                raise CassetteMiss(
                    f"No cassette entry for agent={agent} "
                    f"model={kwargs.get('model')} key={key}. "
                    f"Run with --record to capture."
                )
            return self._fake_response(hit)

        if self.mode == "auto" and hit is not None:
            return self._fake_response(hit)

        # record (forced) or auto-miss → real call + record
        t0 = time.monotonic()
        real = await self._original(**kwargs)
        latency_ms = int((time.monotonic() - t0) * 1000)

        entry = CassetteEntry(
            model=kwargs.get("model", ""),
            prompt_hash=key,
            prompt_sample=self._sample(kwargs.get("messages")),
            temperature=round(float(kwargs.get("temperature", 0.0) or 0.0), 3),
            schema_hash=self._schema_repr(kwargs.get("response_format")),
            response_content=real.choices[0].message.content or "",
            usage=self._usage_dict(real),
            latency_ms=latency_ms,
            recorded_at=datetime.now(timezone.utc).isoformat(),
            recorded_on_provider=self._provider_of(kwargs.get("model", "")),
        )
        entries[key] = entry
        self._dirty.add(agent)
        return real

    # ── Install / uninstall ─────────────────────────────────────────

    def install(self) -> None:
        self._original = litellm.acompletion
        litellm.acompletion = self._shim  # type: ignore[assignment]
        log.info("cassette: installed (mode=%s, root=%s)", self.mode, self.root_dir)

    def uninstall(self) -> None:
        if self._original is not None:
            litellm.acompletion = self._original
            self._original = None

    # ── Post-run analytics (derived without production-code changes) ─

    def all_entries(self) -> list[tuple[str, CassetteEntry]]:
        """Flat list of (agent, entry) across all cassettes."""
        out = []
        for agent, entries in self._files.items():
            for entry in entries.values():
                out.append((agent, entry))
        return out

    def tier_usage(self) -> dict[str, int]:
        """Call count per model string — feeds report.py."""
        counts: dict[str, int] = {}
        for _agent, entry in self.all_entries():
            counts[entry.model] = counts.get(entry.model, 0) + 1
        return counts

    def total_tokens(self) -> int:
        return sum(
            (entry.usage or {}).get("total_tokens") or 0
            for _agent, entry in self.all_entries()
        )

    def per_agent_counts(self) -> dict[str, int]:
        return {a: len(e) for a, e in self._files.items()}

    def avg_latency_ms(self) -> dict[str, float]:
        """Mean latency per agent from recorded entries."""
        out: dict[str, float] = {}
        for agent, entries in self._files.items():
            values = [
                e.latency_ms for e in entries.values()
                if e.latency_ms is not None
            ]
            if values:
                out[agent] = sum(values) / len(values)
        return out


# ── Drift diff (review fix #6) ───────────────────────────────────────

def diff_structured(a_content: str, b_content: str) -> dict:
    """Compare two response strings semantically when both parse as JSON.

    For Monogram all LLM responses are structured-output JSON so this
    is the hot path. Falls back to textual diff for free-form content.
    """
    try:
        a = json.loads(a_content)
        b = json.loads(b_content)
    except (json.JSONDecodeError, TypeError):
        return {
            "kind": "text",
            "changed": a_content != b_content,
            "a_len": len(a_content or ""),
            "b_len": len(b_content or ""),
        }

    return {
        "kind": "json",
        "diff": _deep_diff(a, b),
    }


def _deep_diff(a: Any, b: Any, path: str = "") -> list[dict]:
    """Return a list of diff records between a and b."""
    out: list[dict] = []
    if type(a) is not type(b):
        out.append({"path": path or "/", "kind": "type", "a": repr(a)[:100], "b": repr(b)[:100]})
        return out
    if isinstance(a, dict):
        for k in sorted(set(a.keys()) | set(b.keys())):
            sub = f"{path}/{k}"
            if k not in a:
                out.append({"path": sub, "kind": "added", "b": repr(b[k])[:100]})
            elif k not in b:
                out.append({"path": sub, "kind": "removed", "a": repr(a[k])[:100]})
            else:
                out.extend(_deep_diff(a[k], b[k], sub))
        return out
    if isinstance(a, list):
        if len(a) != len(b):
            out.append({"path": path or "/", "kind": "list_len", "a_len": len(a), "b_len": len(b)})
        for i, (x, y) in enumerate(zip(a, b)):
            out.extend(_deep_diff(x, y, f"{path}[{i}]"))
        return out
    if a != b:
        out.append({"path": path or "/", "kind": "value", "a": repr(a)[:100], "b": repr(b)[:100]})
    return out

"""Sharded vector index: shard key = (area, month); chunk_id self-routes; per-chunk incremental reindex."""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from . import embeddings
from .secret_filter import redact

log = logging.getLogger("monogram.semantic_index")

INDEXABLE_AREAS = ("wiki", "projects", "life", "daily", "identity")
EXCLUDE_PREFIXES = (
    "life/credentials/",
    "raw/",
    "index/",
    "graph/",
    "mono/",
    "log/",
    ".monogram/",
)

MANIFEST_PATH = "index/manifest.json"
_EXCLUDE_FILES = ("wiki/index.md",)  # navigation-only files; not knowledge worth indexing
_MANIFEST_VERSION = 1
_LONG_NOTE_CHARS = 6000     # split longer notes on H2 only (~1.5k tokens)
_EXCERPT_CHARS = 200
_TEXT_STORE_CHARS = 2000    # full chunk text stored for BM25 (bounds index size)


def area_of(path: str) -> str | None:
    p = path.lstrip("/")
    if any(p.startswith(pre) for pre in EXCLUDE_PREFIXES):
        return None
    head = p.split("/", 1)[0]
    return head if head in INDEXABLE_AREAS else None


def is_indexable(path: str) -> bool:
    return (
        path.endswith(".md")
        and path not in _EXCLUDE_FILES
        and area_of(path) is not None
    )


def _month_from_created(created) -> str | None:
    # Handles both ISO string and YAML-parsed date/datetime (unquoted `created: 2026-05-13`).
    if hasattr(created, "strftime"):          # date / datetime
        return created.strftime("%Y-%m")
    if isinstance(created, str) and len(created) >= 7 and created[4] == "-":
        return created[:7]
    return None


def created_month(meta: dict | None, path: str) -> str:
    # Stable shard home: frontmatter `created` → daily/ folder date → 'undated'.
    month = _month_from_created((meta or {}).get("created"))
    if month:
        return month
    m = re.search(r"daily/(\d{4})-(\d{2})-\d{2}/", path)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    return "undated"


def shard_key(area: str, month: str) -> str:
    return f"{area}/{month}"


def shard_file(key: str) -> str:
    return f"index/vec/{key}.jsonl"


def make_chunk_id(area: str, month: str, path: str, n: int | str) -> str:
    # `<area>/<month>#<path>#<n>` embeds the shard key so the chunk self-routes.
    return f"{area}/{month}#{path}#{n}"


def shard_of_chunk(chunk_id: str) -> str:
    return chunk_id.split("#", 1)[0]


def chunk_key_of(chunk_id: str) -> str:
    # Location-independent identity so a chunk keeps its key when it re-homes to a different shard.
    parts = chunk_id.split("#", 2)
    return f"{parts[1]}#{parts[2]}" if len(parts) == 3 else chunk_id


def content_hash(redacted_text: str) -> str:
    # Hash over *redacted* text — a credential must never ride along in an 'unchanged' chunk.
    return hashlib.sha256(redacted_text.encode("utf-8")).hexdigest()


def _title_of(path: str) -> str:
    return Path(path).stem.replace("-", " ").replace("_", " ").strip() or path


_DATED_H2 = re.compile(r"(?m)^##\s+\d{4}-\d{2}-\d{2}")


def _heading_month(heading: str) -> str | None:
    m = re.match(r"(\d{4})-(\d{2})-\d{2}", heading)
    return f"{m.group(1)}-{m.group(2)}" if m else None


def chunk_note(
    path: str, redacted_body: str, note_month: str = "undated"
) -> list[tuple[int, str, str, str, str]]:
    # Dated `## YYYY-MM-DD` headings re-home chunks to their own month (life log fan-out).
    area = area_of(path) or ""
    title = _title_of(path)
    header = f"{area} · {title}".strip(" ·")

    body = redacted_body.strip()
    if not body:
        return []

    if len(body) <= _LONG_NOTE_CHARS and not _DATED_H2.search(body):
        parts = [(title, body)]
    else:
        parts = _split_on_h2(body, title)

    out: list[tuple[int, str, str, str, str, str]] = []
    for n, (heading, text) in enumerate(parts):
        month = _heading_month(heading) or note_month
        embed_text = f"{header} — {heading}\n{text}" if heading != title else f"{header}\n{text}"
        out.append((n, month, heading, text, embed_text, content_hash(f"{heading}\n{text}")))
    return out


def _split_on_h2(body: str, title: str) -> list[tuple[str, str]]:
    parts: list[tuple[str, str]] = []
    cur_heading = title
    cur: list[str] = []
    for line in body.splitlines():
        m = re.match(r"^##\s+(.*)", line)
        if m:
            if cur:
                parts.append((cur_heading, "\n".join(cur).strip()))
            cur_heading, cur = m.group(1).strip(), []
        else:
            cur.append(line)
    if cur:
        parts.append((cur_heading, "\n".join(cur).strip()))
    return [(h, t) for h, t in parts if t]


def empty_manifest() -> dict:
    return {
        "version": _MANIFEST_VERSION,
        "model": None,
        "dims": None,
        "updated": None,
        "shards": {},
    }


def _current_month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _vault_dir(refresh: bool):
    from . import cli_search
    return cli_search._refresh_vault_cache(max_age_minutes=0 if refresh else 60)


def _read_manifest(vault_dir: Path) -> dict:
    try:
        return json.loads((vault_dir / MANIFEST_PATH).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _read_line_records(vault_dir: Path) -> dict[str, list[dict]]:
    by_path: dict[str, list[dict]] = {}
    vec_root = vault_dir / "index" / "vec"
    if not vec_root.exists():
        return by_path
    for f in vec_root.rglob("*.jsonl"):
        try:
            for line in f.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                by_path.setdefault(rec["path"], []).append(rec)
        except (OSError, json.JSONDecodeError, KeyError) as e:
            log.warning("semantic_index: skipped shard %s (%s)", f, e)
    return by_path


def _build_desired_chunks(vault_dir: Path) -> dict[str, dict]:
    from . import github_store

    desired: dict[str, dict] = {}
    for f in vault_dir.rglob("*.md"):
        rel = f.relative_to(vault_dir).as_posix()
        if not is_indexable(rel):
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        meta, body = github_store.parse_metadata(text)
        body = redact(body)
        area = area_of(rel)
        note_month = created_month(meta, rel)
        for n, month, heading, text, embed_text, chash in chunk_note(rel, body, note_month):
            desired[f"{rel}#{n}"] = {
                "path": rel, "area": area, "month": month, "n": n,
                "heading": heading, "text": text, "embed": embed_text, "hash": chash,
            }
    return desired


def _existing_chunk_lines(vault_dir: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for recs in _read_line_records(vault_dir).values():
        for rec in recs:
            cid = rec.get("chunk_id")
            if cid:
                out[chunk_key_of(cid)] = rec
    return out


async def reindex(*, refresh: bool = True, dry_run: bool = False) -> dict:
    vault_dir = _vault_dir(refresh)
    model = embeddings.resolve_model()
    desired = _build_desired_chunks(vault_dir)
    existing = _existing_chunk_lines(vault_dir)

    prev_model = _read_manifest(vault_dir).get("model")
    if prev_model and prev_model != model:
        # Vector spaces aren't comparable across models; must re-embed everything.
        log.warning("embedding model changed (%s → %s); full reindex", prev_model, model)
        existing = {}

    changed = [ck for ck in desired if existing.get(ck, {}).get("hash") != desired[ck]["hash"]]
    new_lines = {} if (dry_run or not changed) else await _embed_chunks(desired, changed)

    final_by_shard, dirty, _changed, deleted = plan_shards(
        desired, existing, new_lines, dry_run=dry_run
    )

    stats = {
        "chunks": len(desired),
        "embedded": len(changed),
        "reused": len(desired) - len(changed),
        "deleted": len(deleted),
        "dirty_shards": dirty,
        "committed": False,
    }
    if dry_run or not dirty:
        return stats

    writes = _render_writes(final_by_shard, dirty, model=model)
    from . import github_store
    stats["committed"] = github_store.write_atomic(
        writes, f"monogram reindex: {len(changed)} embedded, {len(dirty)} shards"
    )
    if stats["committed"]:
        _mirror_to_cache(vault_dir, writes)  # keep local cache consistent so next query sees new index
    return stats


def _mirror_to_cache(vault_dir: Path, writes: dict[str, str]) -> None:
    for rel, content in writes.items():
        dest = vault_dir / rel
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content, encoding="utf-8")
        except OSError as e:
            log.warning("semantic_index: cache mirror failed for %s (%s)", rel, e)


async def _embed_chunks(desired: dict, changed: list[str]) -> dict[str, dict]:
    texts = [desired[ck]["embed"] for ck in changed]
    vectors = await embeddings.embed_documents(texts)
    out: dict[str, dict] = {}
    for ck, vec in zip(changed, vectors):
        c = desired[ck]
        out[ck] = {
            "chunk_id": make_chunk_id(c["area"], c["month"], c["path"], c["n"]),
            "path": c["path"], "heading": c["heading"],
            "text": c["text"][:_TEXT_STORE_CHARS],
            "hash": c["hash"], "vec": embeddings.prepare_for_storage(vec),
        }
    return out


def plan_shards(
    desired: dict[str, dict],
    existing: dict[str, dict],
    new_lines: dict[str, dict],
    *,
    dry_run: bool = False,
) -> tuple[dict[str, list[dict]], list[str], list[str], list[str]]:
    # Unchanged+unmoved chunks reproduce byte-identical lines so their shard is detected clean.
    changed = [ck for ck in desired if existing.get(ck, {}).get("hash") != desired[ck]["hash"]]
    deleted = [ck for ck in existing if ck not in desired]
    changed_set = set(changed)

    final: dict[str, list[dict]] = {}
    for ck, c in desired.items():
        if ck in changed_set:
            line = new_lines.get(ck)
            if line is None:
                if not dry_run:
                    continue  # changed but no embedding produced (e.g. empty); drop
                line = {  # planning placeholder so dirty detection still works
                    "chunk_id": make_chunk_id(c["area"], c["month"], c["path"], c["n"]),
                    "path": c["path"], "hash": c["hash"],
                }
        else:
            line = {**existing[ck], "chunk_id": make_chunk_id(c["area"], c["month"], c["path"], c["n"])}
        final.setdefault(shard_of_chunk(line["chunk_id"]), []).append(line)

    initial: dict[str, list[dict]] = {}
    for rec in existing.values():
        cid = rec.get("chunk_id")
        if cid:
            initial.setdefault(shard_of_chunk(cid), []).append(rec)

    dirty = sorted(
        sk for sk in set(initial) | set(final)
        if _shard_repr(final.get(sk, [])) != _shard_repr(initial.get(sk, []))
    )
    return final, dirty, changed, deleted


def _shard_repr(lines: list[dict]) -> str:
    return "\n".join(sorted(json.dumps(r, sort_keys=True, ensure_ascii=False) for r in lines))


def _infer_dims(final_by_shard: dict[str, list[dict]]) -> int | None:
    for lines in final_by_shard.values():
        for r in lines:
            v = r.get("vec")
            if isinstance(v, str):
                try:
                    return len(base64.b64decode(v))
                except Exception:
                    return None
    return None


def _render_writes(
    final_by_shard: dict[str, list[dict]], dirty: list[str], *, model: str | None = None
) -> dict[str, str]:
    manifest = empty_manifest()
    manifest["model"] = model
    manifest["dims"] = _infer_dims(final_by_shard)
    writes: dict[str, str] = {}

    all_keys = set(final_by_shard) | set(dirty)
    cur = _current_month()
    for sk in sorted(all_keys):
        lines = sorted(final_by_shard.get(sk, []), key=lambda r: r["chunk_id"])
        content = "\n".join(json.dumps(r, sort_keys=True, ensure_ascii=False) for r in lines)
        if sk in dirty:
            writes[shard_file(sk)] = content + ("\n" if content else "")
        if lines:
            manifest["shards"][sk] = {
                "path": shard_file(sk),
                "n": len(lines),
                "bytes": len(content),
                "sealed": not sk.endswith(cur),  # current-month shard is write-hot; past shards are sealed
            }
    manifest["updated"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    writes[MANIFEST_PATH] = json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
    return writes


async def semantic_search(query: str, k: int = 8, *, areas: list[str] | None = None,
                          refresh: bool = False, hybrid: bool = True,
                          rerank: bool | None = None) -> list[dict]:
    vault_dir = _vault_dir(refresh)
    records = _load_query_records(vault_dir, areas)
    if not records:
        return []
    qvec = await embeddings.embed_query(query)
    if not qvec:
        return []
    q8 = embeddings.quantize_int8(qvec)

    sem_scores = _semantic_scores(q8, records)
    sem_order = sorted(range(len(records)), key=lambda i: -sem_scores[i])
    if not hybrid:
        return [_format(records[i], sem_scores[i]) for i in sem_order[:k]]

    from . import bm25
    q_tokens = bm25.tokenize(query)
    corpus = [bm25.tokenize(f"{r.get('heading','')} {r.get('text','')}") for r in records]
    lex_scores = bm25.bm25_scores(q_tokens, corpus)
    lex_order = [i for i in sorted(range(len(records)), key=lambda i: -lex_scores[i]) if lex_scores[i] > 0]

    fused = bm25.reciprocal_rank_fusion([
        [records[i]["chunk_id"] for i in sem_order],
        [records[i]["chunk_id"] for i in lex_order],
    ])
    by_id = {r["chunk_id"]: r for r in records}
    fused_sorted = sorted(fused.items(), key=lambda t: -t[1])

    if rerank is None:
        rerank = _rerank_enabled()
    if rerank:
        pool = [by_id[cid] for cid, _ in fused_sorted[: max(k * 4, 20)]]
        from .reranker import rerank_records
        reranked = rerank_records(query, pool, k)
        if reranked:  # empty if reranker unavailable — fall through to RRF order
            return [_format(r, r.get("_rerank", 0.0)) for r in reranked]

    return [_format(by_id[cid], score) for cid, score in fused_sorted[:k]]


def _rerank_enabled() -> bool:
    try:
        from .vault_config import load_vault_config
        return bool(load_vault_config().embedding_rerank)
    except Exception:
        return False


def _format(rec: dict, score: float) -> dict:
    excerpt = re.sub(r"\s+", " ", rec.get("text", "")).strip()[:_EXCERPT_CHARS]
    return {
        "path": rec["path"], "heading": rec.get("heading", ""),
        "excerpt": excerpt, "score": float(score),
    }


def _load_query_records(vault_dir: Path, areas: list[str] | None) -> list[dict]:
    # Modal vec length (not fixed dim) keeps query correct for any model and drops model-switch stragglers.
    from collections import Counter

    recs = [
        rec
        for group in _read_line_records(vault_dir).values()
        for rec in group
        if isinstance(rec.get("vec"), str)
    ]
    if not recs:
        return []
    modal_len = Counter(len(r["vec"]) for r in recs).most_common(1)[0][0]

    out: list[dict] = []
    dim_skipped = 0
    for rec in recs:
        if len(rec["vec"]) != modal_len:
            dim_skipped += 1
            continue
        if areas and shard_of_chunk(rec.get("chunk_id", "")).split("/")[0] not in areas:
            continue
        out.append(rec)
    if dim_skipped:
        log.warning(
            "semantic: %d/%d records skipped (vector dim != modal); "
            "run `monogram reindex` after an embedding-model change",
            dim_skipped, len(recs),
        )
    return out


def _semantic_scores(q8: list[int], records: list[dict]) -> list[float]:
    # numpy matmul if available (uniform-dim guaranteed by modal filter); else pure loop.
    try:
        import numpy as np

        q = np.asarray(q8, dtype=np.int16)
        mat = np.frombuffer(
            b"".join(base64.b64decode(r["vec"]) for r in records),
            dtype=np.int8,
        ).reshape(len(records), -1).astype(np.int16)
        return (mat @ q).astype(float).tolist()
    except Exception:
        return [float(embeddings.dot_int8(q8, embeddings.decode_vec(r["vec"]))) for r in records]

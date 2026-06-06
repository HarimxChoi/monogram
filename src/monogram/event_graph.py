"""Event graph: drops & commits as nodes with causal edges. Bi-temporal — close, never delete."""
from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import PurePosixPath

log = logging.getLogger("monogram.event_graph")

NODES_PATH = "graph/nodes.jsonl"
EDGES_PATH = "graph/edges.jsonl"

_MIN_SIGNALS = 2          # motivated_by candidate needs ≥2 independent signals
_VERIFY_CONFIDENCE = 0.7  # Flash model must clear this confidence to accept a motivated_by edge

_AREA_NODE_TYPE = {
    "wiki": "concept", "projects": "project", "life": "log",
    "daily": "log", "identity": "concept",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def node(node_id: str, type: str, label: str, **extra) -> dict:
    return {"id": node_id, "type": type, "label": label, **extra}


def edge_id(subject: str, predicate: str, obj: str) -> str:
    return hashlib.sha1(f"{subject}|{predicate}|{obj}".encode("utf-8")).hexdigest()[:16]


def make_edge(
    subject: str, predicate: str, obj: str, *,
    valid_at: str, fact: str = "", evidence: str = "", source: str = "",
    status: str = "confirmed", now: str | None = None,
) -> dict:
    # valid_at = when the fact held; invalid_at/expired_at stay None until superseded.
    now = now or _now_iso()
    return {
        "id": edge_id(subject, predicate, obj),
        "subject": subject, "predicate": predicate, "object": obj,
        "fact": fact, "evidence": evidence, "source": source, "status": status,
        "valid_at": valid_at, "invalid_at": None,
        "created_at": now, "expired_at": None,
    }


def invalidate(edge: dict, at: str, now: str | None = None) -> dict:
    return {**edge, "invalid_at": at, "expired_at": now or _now_iso()}


def current_edges(edges: list[dict]) -> list[dict]:
    return [e for e in edges if e.get("invalid_at") is None and e.get("expired_at") is None]


def _title(path: str) -> str:
    return PurePosixPath(path).stem.replace("-", " ").replace("_", " ").strip() or path


def _note_node(path: str) -> tuple[str, dict]:
    area = path.split("/", 1)[0]
    ntype = _AREA_NODE_TYPE.get(area, "concept")
    nid = f"note:{path}"
    return nid, node(nid, ntype, _title(path), path=path)


def _is_note_path(dest: str) -> bool:
    return (
        dest not in ("daily_only", "(redacted)", "")
        and "/" in dest
        and dest.endswith(".md")
        and not dest.startswith("life/credentials/")   # do not graph credentials
    )


_DROP_LINE = re.compile(r"\*\*(?P<kind>.+?)\*\*\s*→\s*`?(?P<dest>[^`\n]+?)`?\s*$")


def parse_drops(date: str, drops_md: str) -> tuple[list[dict], list[dict]]:
    nodes: list[dict] = []
    edges: list[dict] = []
    n = 0
    for block in re.split(r"(?m)^##\s+", drops_md or ""):
        block = block.strip()
        if not block:
            continue
        lines = block.splitlines()
        tm = re.match(r"(\d{2}:\d{2})", lines[0].strip())
        if not tm:
            continue
        hhmm = tm.group(1)
        kind = dest = None
        summary = ""
        for i, ln in enumerate(lines[1:], start=1):
            dm = _DROP_LINE.match(ln.strip())
            if dm:
                kind, dest = dm.group("kind").strip(), dm.group("dest").strip()
                summary = " ".join(x.strip() for x in lines[i + 1:]).strip()
                break
        if kind is None:
            continue
        when = f"{date} {hhmm}"
        drop_id = f"drop:{date}#{n}"
        nodes.append(node(drop_id, "drop", f"{kind} @ {hhmm}", time=when,
                          kind=kind, summary=summary[:200]))
        if _is_note_path(dest):
            note_id, note_nd = _note_node(dest)
            nodes.append(note_nd)
            edges.append(make_edge(drop_id, "documents", note_id, valid_at=when,
                                   source=f"daily/{date}/drops.md"))
        n += 1
    return nodes, edges


def commit_graph(rec: dict, repo_project_map: dict | None = None) -> tuple[list[dict], list[dict]]:
    nodes: list[dict] = []
    edges: list[dict] = []
    repo, sha = rec.get("repo", ""), rec.get("sha", "")
    when = rec.get("time", "")
    cid = f"commit:{repo}#{sha}"
    nodes.append(node(cid, "commit", (rec.get("message") or "")[:200], time=when, repo=repo,
                      ctype=rec.get("type"), scope=rec.get("scope"),
                      breaking=bool(rec.get("breaking")), is_merge=bool(rec.get("is_merge"))))

    repo_id = f"repo:{repo}"
    nodes.append(node(repo_id, "repo", repo))
    edges.append(make_edge(cid, "in_repo", repo_id, valid_at=when))

    for person in [rec.get("author")] + list(rec.get("co_authors", [])):
        if not person:
            continue
        pid = f"person:{person}"
        nodes.append(node(pid, "person", person))
        edges.append(make_edge(cid, "authored_by", pid, valid_at=when))

    for parent in rec.get("parents", []):
        edges.append(make_edge(f"commit:{repo}#{parent}", "precedes", cid, valid_at=when))

    project = (repo_project_map or {}).get(repo)
    if project:
        edges.append(make_edge(cid, "implements", f"note:projects/{project}.md", valid_at=when))

    return nodes, edges


def assemble_signal_map(
    *, vector: list[str] | None = None, shared_paths: list[str] | None = None,
    repo_project: str | None = None, issues: list[str] | None = None,
    temporal: list[str] | None = None,
) -> dict[str, set[str]]:
    sm: dict[str, set[str]] = defaultdict(set)
    for t in vector or []:
        sm[t].add("vector")
    for t in shared_paths or []:
        sm[t].add("paths")
    if repo_project:
        sm[repo_project].add("repo_project")
    for t in issues or []:
        sm[t].add("issue")
    for t in temporal or []:
        sm[t].add("temporal")
    return dict(sm)


def candidates_with_min_signals(
    signal_map: dict[str, set[str]], min_signals: int = _MIN_SIGNALS
) -> list[tuple[str, list[str]]]:
    out = [
        (tid, sorted(sigs))
        for tid, sigs in signal_map.items()
        if len(sigs) >= min_signals
    ]
    out.sort(key=lambda t: (-len(t[1]), t[0]))
    return out


_ISSUE_RE = re.compile(r"#(\d+)")
_BACKTICK_RE = re.compile(r"`([^`\n]+)`")


def extract_issue_refs(text: str) -> set[str]:
    return {f"#{m}" for m in _ISSUE_RE.findall(text or "")}


def extract_paths(text: str) -> set[str]:
    out: set[str] = set()
    for tok in _BACKTICK_RE.findall(text or ""):
        tok = tok.strip()
        if "/" in tok or re.search(r"\.[A-Za-z0-9]{1,5}$", tok):
            out.add(tok)
    out.update(re.findall(r"(?<![\w/])([\w.-]+/[\w./-]+\.[A-Za-z0-9]{1,5})", text or ""))
    return out


def path_overlap(a: set[str], b: set[str]) -> bool:
    # Basename match catches note refs that omit the directory prefix.
    if a & b:
        return True
    return bool({p.rsplit("/", 1)[-1] for p in a} & {p.rsplit("/", 1)[-1] for p in b})


_REVERTS_RE = re.compile(r"This reverts commit ([0-9a-f]{7,40})", re.IGNORECASE)


def is_revert(rec: dict) -> bool:
    return (
        rec.get("type") == "revert"
        or (rec.get("message") or "").startswith("Revert ")
        or bool(_REVERTS_RE.search(rec.get("full_message") or ""))
    )


def reverted_sha(rec: dict) -> str | None:
    m = _REVERTS_RE.search(rec.get("full_message") or "")
    return m.group(1)[:7] if m else None


def supersede_reverts(commits: list[dict], emap: dict[str, dict], *, now: str | None = None) -> int:
    # Close edges of reverted commits — never delete (bi-temporal invariant).
    now = now or _now_iso()
    closed = 0
    for rec in commits:
        if not is_revert(rec):
            continue
        sha = reverted_sha(rec)
        if not sha:
            continue
        target = f"commit:{rec.get('repo', '')}#{sha}"
        for eid, e in list(emap.items()):
            if (e.get("subject") == target
                    and e.get("predicate") in ("motivated_by", "implements")
                    and e.get("invalid_at") is None):
                emap[eid] = invalidate(e, rec.get("time", ""), now)
                closed += 1
    return closed


def read_jsonl_map(text: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "id" in rec:
            out[rec["id"]] = rec
    return out


def dump_jsonl_map(records: dict[str, dict]) -> str:
    lines = [json.dumps(records[k], sort_keys=True, ensure_ascii=False) for k in sorted(records)]
    return ("\n".join(lines) + "\n") if lines else ""


def upsert_nodes(existing: dict[str, dict], new: list[dict]) -> dict[str, dict]:
    for nd in new:
        existing[nd["id"]] = {**existing.get(nd["id"], {}), **nd}
    return existing


def merge_edges(existing: dict[str, dict], new: list[dict]) -> dict[str, dict]:
    # setdefault keeps the first-seen edge idempotent; supersession is handled separately.
    for e in new:
        existing.setdefault(e["id"], e)
    return existing


def build_graph(date: str, *, repo_project_map: dict | None = None) -> dict:
    from . import github_store

    drops_md = github_store.read(f"daily/{date}/drops.md")
    commits_jsonl = github_store.read(f"daily/{date}/commits.jsonl")

    nodes, edges = parse_drops(date, drops_md)
    records: list[dict] = []
    for line in commits_jsonl.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        records.append(rec)
        cn, ce = commit_graph(rec, repo_project_map)
        nodes += cn
        edges += ce

    nmap = read_jsonl_map(github_store.read(NODES_PATH))
    emap = read_jsonl_map(github_store.read(EDGES_PATH))
    before = (len(nmap), len(emap))
    upsert_nodes(nmap, nodes)
    merge_edges(emap, edges)
    reverted = supersede_reverts(records, emap)

    committed = False
    if (len(nmap), len(emap)) != before or reverted:
        writes = {NODES_PATH: dump_jsonl_map(nmap), EDGES_PATH: dump_jsonl_map(emap)}
        committed = github_store.write_atomic(
            writes,
            f"monogram graph: {date} (+{len(nmap) - before[0]} nodes, "
            f"+{len(emap) - before[1]} edges, {reverted} reverted)",
        )
    return {
        "date": date, "nodes": len(nmap), "edges": len(emap),
        "new_nodes": len(nmap) - before[0], "new_edges": len(emap) - before[1],
        "reverted": reverted, "committed": committed,
    }


async def _verify_motivated_by(commit_label: str, target_label: str, target_excerpt: str) -> tuple[bool, str, float]:
    # The cheap model only filters deterministic candidates; lazy-imports to keep module importable.
    from pydantic import BaseModel

    from . import llm
    from .models import get_model

    class Verdict(BaseModel):
        motivated: bool
        evidence: str
        confidence: float

    prompt = (
        "A commit and a knowledge item are given. Did the commit do work that was "
        "motivated by / about THIS item? Answer only from the text; if unsure say false.\n\n"
        f"COMMIT: {commit_label}\n\nITEM: {target_label}\n{target_excerpt}\n"
    )
    try:
        v = await llm.extract(prompt, Verdict, model=get_model("mid"),
                              temperature=0, agent_tag="graph_link")
    except Exception as e:
        log.warning("event_graph: motivated_by verify failed (%s)", e)
        return False, "", 0.0
    return bool(v.motivated), v.evidence, float(v.confidence)


def _read_cached_note(vault_dir, path: str) -> str:
    try:
        return (vault_dir / path).read_text(encoding="utf-8")
    except OSError:
        return ""


async def link_motivated_by(date: str, *, repo_project_map: dict | None = None, k: int = 5) -> dict:
    # ≥2-signal gating means e.g. vector ∩ shared-path; Flash verify is the precision gate on top.
    from . import github_store, semantic_index
    from .commit_parse import KNOWN_TYPES

    commits_jsonl = github_store.read(f"daily/{date}/commits.jsonl")
    emap = read_jsonl_map(github_store.read(EDGES_PATH))
    nmap = read_jsonl_map(github_store.read(NODES_PATH))
    before_e, before_n = len(emap), len(nmap)
    code_types = set(KNOWN_TYPES) - {"docs", "chore", "style", "ci", "build"}
    vault_dir = semantic_index._vault_dir(False)

    for line in commits_jsonl.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("is_merge") or (rec.get("type") and rec["type"] not in code_types):
            continue

        query = " ".join(filter(None, [
            rec.get("full_message", rec.get("message", "")),
            " ".join(f["path"] for f in rec.get("files", [])[:20]),
        ]))
        hits = await semantic_index.semantic_search(query, k=k)
        hit_by_id = {f"note:{h['path']}": h for h in hits}
        repo_project = (repo_project_map or {}).get(rec.get("repo", ""))
        proj_target = f"note:projects/{repo_project}.md" if repo_project else None

        commit_issues = set(rec.get("issues", []))
        commit_files = {f["path"] for f in rec.get("files", []) if f.get("path")}
        issue_targets, path_targets = [], []
        for nid, h in hit_by_id.items():
            note_text = _read_cached_note(vault_dir, h["path"])
            if commit_issues & extract_issue_refs(note_text):
                issue_targets.append(nid)
            if commit_files and path_overlap(extract_paths(note_text), commit_files):
                path_targets.append(nid)

        signal_map = assemble_signal_map(
            vector=list(hit_by_id), repo_project=proj_target,
            issues=issue_targets, shared_paths=path_targets,
        )
        candidates = candidates_with_min_signals(signal_map)

        cid = f"commit:{rec.get('repo','')}#{rec.get('sha','')}"
        commit_label = (rec.get("full_message") or rec.get("message") or "")[:300]
        for target_id, signals in candidates:
            hit = hit_by_id.get(target_id)
            label = (hit or {}).get("heading") or nmap.get(target_id, {}).get("label", target_id)
            excerpt = (hit or {}).get("excerpt", "") or nmap.get(target_id, {}).get("path", "")
            ok, evidence, conf = await _verify_motivated_by(commit_label, label, excerpt)
            if not (ok and conf >= _VERIFY_CONFIDENCE):
                continue
            if hit and target_id not in nmap:   # materialize note node if not yet in graph
                nid, nd = _note_node(hit["path"])
                nmap[nid] = {**nd, "label": label or nd["label"]}
            e = make_edge(cid, "motivated_by", target_id, valid_at=rec.get("time", ""),
                          evidence=evidence[:300], fact=f"signals={','.join(signals)}; conf={conf:.2f}",
                          source=f"daily/{date}/commits.jsonl")
            emap.setdefault(e["id"], e)

    committed = False
    writes: dict[str, str] = {}
    if len(emap) != before_e:
        writes[EDGES_PATH] = dump_jsonl_map(emap)
    if len(nmap) != before_n:
        writes[NODES_PATH] = dump_jsonl_map(nmap)
    if writes:
        committed = github_store.write_atomic(
            writes, f"monogram graph: {date} motivated_by (+{len(emap) - before_e} edges)"
        )
    return {"date": date, "new_edges": len(emap) - before_e, "edges": len(emap), "committed": committed}

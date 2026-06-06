"""Graph-aware retrieval: hybrid seed → PPR + centrality → MMR → notes + N-hop neighborhood."""
from __future__ import annotations

import logging
from collections import defaultdict

log = logging.getLogger("monogram.graph_search")


def build_adjacency(edges: list[dict]) -> dict[str, list[str]]:
    # Undirected: direction doesn't matter for the relatedness walk; only pass current edges.
    adj: dict[str, set] = defaultdict(set)
    for e in edges:
        s, o = e.get("subject"), e.get("object")
        if s and o:
            adj[s].add(o)
            adj[o].add(s)
    return {k: list(v) for k, v in adj.items()}


def personalized_pagerank(
    adj: dict[str, list[str]],
    seeds: dict[str, float],
    *,
    alpha: float = 0.5,
    iters: int = 30,
    tol: float = 1e-6,
) -> dict[str, float]:
    # r = α·s + (1−α)·W·r; dangling mass teleports back to seeds to keep row-stochastic.
    nodes = set(adj) | set(seeds)
    for nbrs in adj.values():
        nodes.update(nbrs)
    if not nodes:
        return {}

    total = sum(seeds.values()) or 1.0
    s = {n: seeds.get(n, 0.0) / total for n in nodes}
    r = dict(s)
    deg = {n: len(adj.get(n, ())) for n in nodes}

    for _ in range(iters):
        nr = {n: alpha * s[n] for n in nodes}
        dangling = 0.0
        for u in nodes:
            ru = r[u]
            if ru == 0.0:
                continue
            d = deg[u]
            if d == 0:
                dangling += ru  # dangling node: mass teleports to seeds
                continue
            share = (1 - alpha) * ru / d
            for v in adj[u]:
                nr[v] += share
        if dangling:
            for n in nodes:
                nr[n] += (1 - alpha) * dangling * s[n]
        if sum(abs(nr[n] - r[n]) for n in nodes) < tol:
            r = nr
            break
        r = nr
    return r


def _normalize(scores: dict[str, float]) -> dict[str, float]:
    mx = max(scores.values(), default=0.0)
    return {k: v / mx for k, v in scores.items()} if mx else scores


def mmr(candidates: list[str], relevance: dict[str, float], sim, k: int, *, lam: float = 0.7) -> list[str]:
    # lam=1 → pure relevance, lam=0 → pure diversity; greedy selection by MMR score.
    selected: list[str] = []
    remaining = list(candidates)
    while remaining and len(selected) < k:
        best, best_score = None, float("-inf")
        for c in remaining:
            penalty = max((sim(c, s) for s in selected), default=0.0)
            score = lam * relevance.get(c, 0.0) - (1 - lam) * penalty
            if score > best_score:
                best, best_score = c, score
        selected.append(best)
        remaining.remove(best)
    return selected


def _cos_int8(a: list[int], b: list[int]) -> float:
    if not a or not b:
        return 0.0
    import math
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


def label_propagation(adj: dict[str, list[str]], iters: int = 20) -> dict[str, str]:
    # Ties break to the smallest label for deterministic output (stable in git).
    from collections import Counter

    labels = {n: n for n in adj}
    nodes = sorted(adj)
    for _ in range(iters):
        changed = False
        for n in nodes:
            nbrs = adj[n]
            if not nbrs:
                continue
            counts = Counter(labels[x] for x in nbrs)
            best = min(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0]
            if labels[n] != best:
                labels[n] = best
                changed = True
        if not changed:
            break
    return labels


def communities(adj: dict[str, list[str]], *, min_size: int = 3, prefix: str | None = None) -> list[list[str]]:
    from collections import defaultdict

    groups: dict[str, list[str]] = defaultdict(list)
    for node, label in label_propagation(adj).items():
        if prefix and not node.startswith(prefix):
            continue
        groups[label].append(node)
    return sorted(
        (sorted(g) for g in groups.values() if len(g) >= min_size),
        key=lambda g: (-len(g), g[0]),
    )


async def moc(min_size: int = 3, *, refresh: bool = False) -> list[list[dict]]:
    from . import event_graph, semantic_index

    vault_dir = semantic_index._vault_dir(refresh)
    nmap = event_graph.read_jsonl_map(_read(vault_dir, event_graph.NODES_PATH))
    edges = event_graph.current_edges(
        list(event_graph.read_jsonl_map(_read(vault_dir, event_graph.EDGES_PATH)).values())
    )
    adj = build_adjacency(edges)
    out: list[list[dict]] = []
    for cluster in communities(adj, min_size=min_size, prefix="note:"):
        out.append([
            {"id": n, "label": nmap.get(n, {}).get("label", n), "path": nmap.get(n, {}).get("path", "")}
            for n in cluster
        ])
    return out


async def graph_search(
    query: str, k: int = 8, *, alpha: float = 0.5,
    w_sem: float = 0.45, w_ppr: float = 0.35, w_cen: float = 0.2,
    mmr_lambda: float = 0.7, hops: int = 1, refresh: bool = False,
) -> list[dict]:
    from . import event_graph, semantic_index

    hits = await semantic_index.semantic_search(query, k=max(k, 15), refresh=refresh)
    if not hits:
        return []

    seeds: dict[str, float] = {}
    sem_score: dict[str, float] = {}
    for rank, h in enumerate(hits):
        nid = f"note:{h['path']}"
        seeds[nid] = seeds.get(nid, 0.0) + 1.0 / (rank + 1)  # rank-weighted seed mass
        sem_score[nid] = max(sem_score.get(nid, 0.0), h["score"])

    vault_dir = semantic_index._vault_dir(refresh)
    nmap = event_graph.read_jsonl_map(_read(vault_dir, event_graph.NODES_PATH))
    edges = event_graph.current_edges(
        list(event_graph.read_jsonl_map(_read(vault_dir, event_graph.EDGES_PATH)).values())
    )
    if not edges:
        return [_flat(h) for h in hits[:k]]  # no graph yet → plain semantic

    adj = build_adjacency(edges)
    ppr = personalized_pagerank(adj, seeds, alpha=alpha)
    centrality = personalized_pagerank(adj, {n: 1.0 for n in adj}, alpha=0.15)  # uniform seeds = global PageRank

    candidates = {n for n, v in ppr.items() if n.startswith("note:") and v > 0} | set(seeds)
    pn = _normalize({n: ppr.get(n, 0.0) for n in candidates})
    sn = _normalize({n: sem_score.get(n, 0.0) for n in candidates})
    cn = _normalize({n: centrality.get(n, 0.0) for n in candidates})
    relevance = {
        n: w_sem * sn.get(n, 0.0) + w_ppr * pn.get(n, 0.0) + w_cen * cn.get(n, 0.0)
        for n in candidates
    }

    vecs = _candidate_vecs(vault_dir, candidates)
    sim = lambda a, b: _cos_int8(vecs.get(a, []), vecs.get(b, []))  # noqa: E731
    by_relevance = sorted(candidates, key=lambda n: -relevance[n])
    ordered = mmr(by_relevance, relevance, sim, k, lam=mmr_lambda)

    out: list[dict] = []
    for nid in ordered:
        node = nmap.get(nid, {})
        out.append({
            "path": node.get("path", nid.replace("note:", "", 1)),
            "label": node.get("label", ""),
            "score": relevance.get(nid, 0.0),
            "neighborhood": _neighborhood(nid, edges, nmap, hops=hops),
        })
    return out


def _candidate_vecs(vault_dir, candidates: set[str]) -> dict[str, list[int]]:
    from . import embeddings, semantic_index

    want = {c.replace("note:", "", 1) for c in candidates}
    out: dict[str, list[int]] = {}
    for r in semantic_index._load_query_records(vault_dir, None):
        nid = f"note:{r['path']}"
        if r["path"] in want and nid not in out:
            out[nid] = embeddings.decode_vec(r["vec"])
    return out


def _neighborhood(nid: str, edges: list[dict], nmap: dict, *, hops: int = 1, limit: int = 8) -> list[dict]:
    out: list[dict] = []
    seen = {nid}
    frontier = {nid}
    for _ in range(max(1, hops)):
        nxt: set[str] = set()
        for e in edges:
            s, o = e["subject"], e["object"]
            for a, b in ((s, o), (o, s)):
                if a in frontier and b not in seen:
                    out.append({"predicate": e["predicate"], "node": nmap.get(b, {}).get("label", b)})
                    seen.add(b)
                    nxt.add(b)
                    if len(out) >= limit:
                        return out
        frontier = nxt
        if not frontier:
            break
    return out


def _flat(h: dict) -> dict:
    return {"path": h["path"], "label": h.get("heading", ""), "score": h.get("score", 0.0),
            "neighborhood": []}


def _read(vault_dir, rel: str) -> str:
    try:
        return (vault_dir / rel).read_text(encoding="utf-8")
    except OSError:
        return ""

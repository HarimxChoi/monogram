"""Graph-aware search — pure adjacency + Personalized PageRank (no graph IO)."""
from monogram import graph_search as GS


def test_build_adjacency_is_undirected():
    adj = GS.build_adjacency([{"subject": "A", "object": "B"}, {"subject": "B", "object": "C"}])
    assert "B" in adj["A"] and "A" in adj["B"]
    assert "C" in adj["B"] and "B" in adj["C"]


def test_ppr_ranks_seed_then_1hop_then_2hop():
    adj = GS.build_adjacency([
        {"subject": "A", "object": "B"},
        {"subject": "A", "object": "D"},
        {"subject": "B", "object": "C"},
    ])
    r = GS.personalized_pagerank(adj, {"A": 1.0}, alpha=0.5)
    assert r["A"] > r["B"] and r["A"] > r["D"]   # seed on top
    assert r["B"] > r["C"]                        # 1-hop beats 2-hop


def test_ppr_higher_restart_concentrates_at_seed():
    adj = GS.build_adjacency([{"subject": "A", "object": "B"}])
    hi = GS.personalized_pagerank(adj, {"A": 1.0}, alpha=0.9)
    lo = GS.personalized_pagerank(adj, {"A": 1.0}, alpha=0.1)
    assert hi["A"] / hi["B"] > lo["A"] / lo["B"]


def test_ppr_empty_and_dangling():
    assert GS.personalized_pagerank({}, {}) == {}
    r = GS.personalized_pagerank({}, {"C": 1.0}, alpha=0.5)   # seeded, no edges
    assert r.get("C", 0.0) > 0.0


# ---- MMR + similarity + N-hop neighborhood -----------------------------------

def test_mmr_diversifies_away_from_duplicates():
    rel = {"a": 1.0, "b": 0.95, "c": 0.8}
    sim = lambda x, y: 1.0 if {x, y} <= {"a", "b"} else 0.0   # a≡b, c distinct
    sel = GS.mmr(["a", "b", "c"], rel, sim, k=2, lam=0.5)
    assert sel == ["a", "c"]                                  # b dropped as a near-dup of a


def test_mmr_pure_relevance_at_lambda_1():
    rel = {"a": 1.0, "b": 0.9, "c": 0.8}
    assert GS.mmr(["a", "b", "c"], rel, lambda x, y: 1.0, k=3, lam=1.0) == ["a", "b", "c"]


def test_cos_int8():
    assert abs(GS._cos_int8([1, 0, 0], [1, 0, 0]) - 1.0) < 1e-9
    assert abs(GS._cos_int8([1, 0, 0], [0, 1, 0])) < 1e-9
    assert GS._cos_int8([], [1]) == 0.0


def test_neighborhood_1hop_vs_2hop():
    nmap = {"A": {"label": "A"}, "B": {"label": "B"}, "C": {"label": "C"}}
    edges = [
        {"subject": "A", "object": "B", "predicate": "documents"},
        {"subject": "B", "object": "C", "predicate": "motivated_by"},
    ]
    one = GS._neighborhood("A", edges, nmap, hops=1)
    two = GS._neighborhood("A", edges, nmap, hops=2)
    assert {n["node"] for n in one} == {"B"}
    assert {n["node"] for n in two} == {"B", "C"}


# ---- community detection (MOC) ----------------------------------------------

def test_communities_two_triangles():
    adj = GS.build_adjacency([
        {"subject": "A", "object": "B"}, {"subject": "B", "object": "C"}, {"subject": "A", "object": "C"},
        {"subject": "D", "object": "E"}, {"subject": "E", "object": "F"}, {"subject": "D", "object": "F"},
    ])
    comms = [set(c) for c in GS.communities(adj, min_size=3)]
    assert len(comms) == 2
    assert {"A", "B", "C"} in comms and {"D", "E", "F"} in comms


def test_communities_filter_by_size_and_prefix():
    adj = GS.build_adjacency([
        {"subject": "note:a", "object": "note:b"},
        {"subject": "note:b", "object": "note:c"},
        {"subject": "note:a", "object": "note:c"},
        {"subject": "commit:x", "object": "note:a"},   # non-note, excluded by prefix
        {"subject": "p", "object": "q"},               # size-2, filtered
    ])
    comms = GS.communities(adj, min_size=3, prefix="note:")
    assert len(comms) == 1
    assert set(comms[0]) == {"note:a", "note:b", "note:c"}

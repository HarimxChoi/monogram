"""Event graph — pure node/edge/parse/merge logic (no github/llm/embeddings)."""
from monogram import event_graph as G


# ---- bi-temporal edge --------------------------------------------------------

def test_edge_id_is_deterministic_from_triple():
    a = G.make_edge("s", "p", "o", valid_at="t1")
    b = G.make_edge("s", "p", "o", valid_at="t2", now="2099-01-01T00:00:00+00:00")
    assert a["id"] == b["id"]            # id depends only on subject|predicate|object
    assert a["invalid_at"] is None and a["expired_at"] is None


def test_invalidate_closes_without_deleting():
    e = G.make_edge("s", "motivated_by", "o", valid_at="t")
    closed = G.invalidate(e, at="2026-06-02", now="2026-06-02T00:00:00+00:00")
    assert closed["invalid_at"] == "2026-06-02" and closed["expired_at"]
    assert G.current_edges([e]) == [e] and G.current_edges([closed]) == []


# ---- drops -------------------------------------------------------------------

DROPS = (
    "## 09:15\n**task** → `projects/monogram.md`\nshipped reindex\n"
    "## 10:00\n**credential** → (redacted)\n"
    "## 11:30\n**personal_thought** → `daily_only`\njust thinking\n"
)


def test_parse_drops_makes_drop_nodes_and_documents_edge():
    nodes, edges = G.parse_drops("2026-06-01", DROPS)
    drops = [n for n in nodes if n["type"] == "drop"]
    assert len(drops) == 3
    assert any(n["id"] == "note:projects/monogram.md" for n in nodes)

    docs = [e for e in edges if e["predicate"] == "documents"]
    assert len(docs) == 1
    assert docs[0]["subject"] == "drop:2026-06-01#0"
    assert docs[0]["object"] == "note:projects/monogram.md"


def test_parse_drops_never_links_credentials_or_daily_only():
    _, edges = G.parse_drops("2026-06-01", DROPS)
    objects = {e["object"] for e in edges}
    assert not any("redacted" in o or "daily_only" in o for o in objects)


# ---- commits -----------------------------------------------------------------

def _commit_rec():
    return {
        "repo": "o/r", "sha": "abc1234", "time": "2026-06-01 09:00",
        "author": "Har", "message": "feat: add reindex", "type": "feat",
        "scope": None, "breaking": False, "is_merge": False,
        "parents": ["par0001"], "co_authors": ["Co <c@x.io>"], "files": [],
    }


def test_commit_graph_deterministic_edges():
    nodes, edges = G.commit_graph(_commit_rec(), {"o/r": "monogram"})
    nids = {n["id"] for n in nodes}
    assert {"commit:o/r#abc1234", "repo:o/r", "person:Har", "person:Co <c@x.io>"} <= nids

    preds = {(e["predicate"], e["subject"], e["object"]) for e in edges}
    assert ("in_repo", "commit:o/r#abc1234", "repo:o/r") in preds
    assert ("authored_by", "commit:o/r#abc1234", "person:Har") in preds
    assert ("precedes", "commit:o/r#par0001", "commit:o/r#abc1234") in preds
    assert ("implements", "commit:o/r#abc1234", "note:projects/monogram.md") in preds


def test_commit_graph_no_implements_without_map():
    _, edges = G.commit_graph(_commit_rec())
    assert not any(e["predicate"] == "implements" for e in edges)


# ---- motivated_by candidates (§4.3 stage 1) ----------------------------------

def test_candidates_require_two_signals():
    sm = G.assemble_signal_map(
        vector=["note:a", "note:b"], repo_project="note:a", issues=["note:c"]
    )
    # note:a → {vector, repo_project} = 2; note:b → {vector} = 1; note:c → {issue} = 1
    cands = G.candidates_with_min_signals(sm)
    assert [t for t, _ in cands] == ["note:a"]
    assert cands[0][1] == ["repo_project", "vector"]


def test_candidates_strongest_first():
    sm = G.assemble_signal_map(
        vector=["a", "b"], shared_paths=["a", "b"], repo_project="a", temporal=["b"]
    )
    # a → 3 signals, b → 3 signals (vector+paths+temporal); both kept, sorted
    cands = G.candidates_with_min_signals(sm)
    assert {t for t, _ in cands} == {"a", "b"}
    assert all(len(sigs) >= 2 for _, sigs in cands)


# ---- jsonl merge -------------------------------------------------------------

def test_jsonl_roundtrip_sorted_by_id():
    m = {"x": {"id": "x", "v": 1}, "a": {"id": "a", "v": 2}}
    text = G.dump_jsonl_map(m)
    assert text.splitlines()[0].startswith('{"id": "a"') or '"id":"a"' in text.splitlines()[0]
    assert G.read_jsonl_map(text) == m


def test_upsert_and_merge_are_idempotent():
    nmap: dict = {}
    G.upsert_nodes(nmap, [G.node("x", "drop", "label-1")])
    G.upsert_nodes(nmap, [G.node("x", "drop", "label-2")])   # same id → update
    assert len(nmap) == 1 and nmap["x"]["label"] == "label-2"

    emap: dict = {}
    e = G.make_edge("s", "p", "o", valid_at="t")
    G.merge_edges(emap, [e])
    G.merge_edges(emap, [e])                                  # same triple → no dup
    assert len(emap) == 1


# ---- recall signals (issue refs / shared paths) ------------------------------

def test_extract_issue_refs():
    assert G.extract_issue_refs("closes #12, also #5 and #12 again") == {"#12", "#5"}


def test_extract_paths():
    paths = G.extract_paths("see `src/foo.py` and `notes here` and bare a/b/c.md plus `cfg`")
    assert "src/foo.py" in paths and "a/b/c.md" in paths
    assert "notes here" not in paths and "cfg" not in paths


def test_path_overlap_by_basename():
    assert G.path_overlap({"writer.py"}, {"src/monogram/agents/writer.py"})
    assert G.path_overlap({"a/b.py"}, {"a/b.py"})
    assert not G.path_overlap({"x.py"}, {"y.py"})


# ---- revert → supersession ---------------------------------------------------

def test_is_revert_and_reverted_sha():
    rec = {"type": "revert", "full_message": 'Revert "feat: x"\n\nThis reverts commit abc1234def.'}
    assert G.is_revert(rec)
    assert G.reverted_sha(rec) == "abc1234"
    assert not G.is_revert({"type": "feat", "message": "feat: y", "full_message": "feat: y"})


def test_supersede_reverts_closes_not_deletes():
    e = G.make_edge("commit:o/r#abc1234", "motivated_by", "note:projects/p.md", valid_at="t1")
    emap = {e["id"]: e}
    revert = {"repo": "o/r", "time": "t2", "type": "revert",
              "full_message": "Revert x\n\nThis reverts commit abc1234."}
    closed = G.supersede_reverts([revert], emap)
    assert closed == 1
    assert len(emap) == 1                          # not deleted
    assert emap[e["id"]]["invalid_at"] == "t2"     # closed
    assert G.current_edges(list(emap.values())) == []

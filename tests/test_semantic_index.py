"""Semantic index — pure routing/hashing/chunking/planning (no API/github)."""
import datetime

from monogram import semantic_index as S


# ---- routing -----------------------------------------------------------------

def test_area_of_indexable_and_excluded():
    assert S.area_of("wiki/foo.md") == "wiki"
    assert S.area_of("projects/p.md") == "projects"
    assert S.area_of("daily/2026-05-01/drops.md") == "daily"
    assert S.area_of("life/health.md") == "life"
    # excluded
    assert S.area_of("life/credentials/openai.md") is None
    assert S.area_of("raw/x.md") is None
    assert S.area_of("index/manifest.json") is None
    assert S.area_of("graph/nodes.jsonl") is None
    assert S.area_of("mono/config.md") is None
    assert S.area_of("random/x.md") is None


def test_is_indexable_requires_md():
    assert S.is_indexable("wiki/foo.md")
    assert not S.is_indexable("wiki/foo.png")
    assert not S.is_indexable("life/credentials/k.md")


# ---- creation month ----------------------------------------------------------

def test_created_month_from_frontmatter():
    assert S.created_month({"created": "2026-05-13T09:00:00Z"}, "wiki/a.md") == "2026-05"


def test_created_month_from_daily_path():
    assert S.created_month({}, "daily/2026-03-09/drops.md") == "2026-03"


def test_created_month_undated_fallback():
    assert S.created_month(None, "wiki/a.md") == "undated"


def test_created_month_handles_yaml_date_objects():
    # Unquoted `created: 2026-05-13` in hand-edited frontmatter parses to a date,
    # not a string — must not fall through to 'undated'.
    assert S.created_month({"created": datetime.date(2026, 5, 13)}, "wiki/a.md") == "2026-05"
    assert S.created_month(
        {"created": datetime.datetime(2026, 5, 13, 9, 0)}, "wiki/a.md"
    ) == "2026-05"


# ---- shard identity / self-routing ------------------------------------------

def test_shard_key_and_file():
    assert S.shard_key("wiki", "2026-05") == "wiki/2026-05"
    assert S.shard_file("wiki/2026-05") == "index/vec/wiki/2026-05.jsonl"


def test_chunk_id_self_routes():
    cid = S.make_chunk_id("wiki", "2026-05", "wiki/a.md", 2)
    assert cid == "wiki/2026-05#wiki/a.md#2"
    assert S.shard_of_chunk(cid) == "wiki/2026-05"


# ---- hashing -----------------------------------------------------------------

def test_content_hash_is_stable_and_sensitive():
    h1 = S.content_hash("hello world")
    assert h1 == S.content_hash("hello world")
    assert h1 != S.content_hash("hello world!")


# ---- chunking ----------------------------------------------------------------

def test_heading_month():
    assert S._heading_month("2026-05-13 09:00 — ran 5k") == "2026-05"
    assert S._heading_month("Section A") is None


def test_chunk_key_roundtrips_with_make_chunk_id():
    cid = S.make_chunk_id("wiki", "2026-05", "wiki/a.md", 3)
    assert S.chunk_key_of(cid) == "wiki/a.md#3"


def test_chunk_note_single_chunk_with_context_header():
    chunks = S.chunk_note("wiki/pose-estimation.md", "A short note about poses.", "2026-05")
    assert len(chunks) == 1
    n, month, heading, text, embed_text, chash = chunks[0]
    assert n == 0
    assert month == "2026-05"                # inherits note_month (no dated heading)
    assert text == "A short note about poses."   # raw chunk text (stored for BM25)
    assert "pose estimation" in embed_text   # title-derived context header
    assert "wiki" in embed_text
    assert len(chash) == 64                  # sha256 hex


def test_chunk_note_empty_body():
    assert S.chunk_note("wiki/a.md", "   ", "2026-05") == []


def test_chunk_note_long_note_splits_on_h2_inheriting_note_month():
    body = "intro\n\n## Section A\n" + ("a " * 2000) + "\n## Section B\n" + ("b " * 2000)
    chunks = S.chunk_note("wiki/big.md", body, "2026-05")
    headings = [h for _, _, h, _, _, _ in chunks]
    assert "Section A" in headings and "Section B" in headings
    assert len(chunks) >= 2
    assert all(m == "2026-05" for _, m, _, _, _, _ in chunks)   # undated headings inherit


def test_chunk_note_dated_log_splits_and_shards_per_entry_month():
    # A SHORT life log with dated entries must still split, each entry by its own
    # month — not pile into one 'undated' shard (the F2 fix).
    body = ("## 2026-04-30 09:00 — ran 5k\nfelt good\n\n"
            "## 2026-05-02 22:00 — read paper\nnotes here")
    chunks = S.chunk_note("life/health.md", body, "undated")
    months = sorted(m for _, m, _, _, _, _ in chunks)
    assert months == ["2026-04", "2026-05"]


def test_chunk_hash_changes_only_for_edited_entry():
    a = S.chunk_note("life/health.md", "## 2026-04-30 09:00 — a\nx", "undated")
    b = S.chunk_note("life/health.md", "## 2026-04-30 09:00 — a\nx EDITED", "undated")
    assert a[0][5] != b[0][5]   # chunk_hash (index 5) differs after an edit


# ---- manifest ----------------------------------------------------------------

def test_empty_manifest_shape():
    m = S.empty_manifest()
    assert m["version"] == S._MANIFEST_VERSION
    assert m["model"] is None and m["dims"] is None   # filled at write time
    assert m["shards"] == {}


# ---- planning (plan_shards: reuse / embed / delete / re-home) ----------------

def _line(area, month, path, n, h, **extra):
    rec = {"chunk_id": S.make_chunk_id(area, month, path, n), "path": path, "hash": h}
    rec.update(extra)
    return rec


def _desired(area, month, path, n, h, text="t"):
    return {"path": path, "area": area, "month": month, "n": n,
            "heading": "x", "excerpt": "x", "hash": h, "text": text}


def test_shard_repr_is_order_independent():
    a = _line("wiki", "2026-05", "wiki/a.md", 0, "H")
    b = _line("wiki", "2026-05", "wiki/b.md", 0, "H")
    assert S._shard_repr([a, b]) == S._shard_repr([b, a])


def test_plan_shards_reuses_unchanged_drops_deleted_adds_new():
    existing = {
        "wiki/a.md#0": _line("wiki", "2026-05", "wiki/a.md", 0, "H_A", vec="AAAA"),
        "wiki/b.md#0": _line("wiki", "2026-05", "wiki/b.md", 0, "H_B", vec="BBBB"),
    }
    desired = {
        "wiki/a.md#0": _desired("wiki", "2026-05", "wiki/a.md", 0, "H_A"),   # unchanged
        "wiki/c.md#0": _desired("wiki", "2026-06", "wiki/c.md", 0, "H_C"),   # new
    }
    new_lines = {"wiki/c.md#0": _line("wiki", "2026-06", "wiki/c.md", 0, "H_C", vec="CCCC")}
    final, dirty, changed, deleted = S.plan_shards(desired, existing, new_lines)

    assert changed == ["wiki/c.md#0"]
    assert deleted == ["wiki/b.md#0"]
    assert {r["path"] for r in final["wiki/2026-05"]} == {"wiki/a.md"}
    assert {r["path"] for r in final["wiki/2026-06"]} == {"wiki/c.md"}
    assert final["wiki/2026-05"][0]["vec"] == "AAAA"          # reused, not re-embedded
    assert set(dirty) == {"wiki/2026-05", "wiki/2026-06"}     # b removed; c added


def test_plan_shards_noop_when_nothing_changed():
    existing = {"wiki/a.md#0": _line("wiki", "2026-05", "wiki/a.md", 0, "H", vec="AAAA")}
    desired = {"wiki/a.md#0": _desired("wiki", "2026-05", "wiki/a.md", 0, "H")}
    final, dirty, changed, deleted = S.plan_shards(desired, existing, {})
    assert changed == [] and deleted == [] and dirty == []
    assert S._shard_repr(final["wiki/2026-05"]) == S._shard_repr([existing["wiki/a.md#0"]])


def test_plan_shards_rehomes_moved_chunk():
    existing = {"wiki/a.md#0": _line("wiki", "2026-05", "wiki/a.md", 0, "H", vec="AAAA")}
    desired = {"wiki/a.md#0": _desired("wiki", "2026-04", "wiki/a.md", 0, "H")}  # month moved
    final, dirty, changed, deleted = S.plan_shards(desired, existing, {})
    assert changed == [] and deleted == []
    assert "wiki/2026-04" in final and "wiki/2026-05" not in final
    assert final["wiki/2026-04"][0]["chunk_id"] == "wiki/2026-04#wiki/a.md#0"
    assert set(dirty) == {"wiki/2026-04", "wiki/2026-05"}


def test_plan_shards_preserves_double_digit_chunk_index():
    existing = {
        "wiki/big.md#2": _line("wiki", "2026-05", "wiki/big.md", 2, "H2", vec="v2"),
        "wiki/big.md#10": _line("wiki", "2026-05", "wiki/big.md", 10, "H10", vec="v10"),
    }
    desired = {
        "wiki/big.md#2": _desired("wiki", "2026-05", "wiki/big.md", 2, "H2"),
        "wiki/big.md#10": _desired("wiki", "2026-05", "wiki/big.md", 10, "H10"),
    }
    final, dirty, _, _ = S.plan_shards(desired, existing, {})
    by_id = {r["chunk_id"]: r["vec"] for r in final["wiki/2026-05"]}
    assert by_id["wiki/2026-05#wiki/big.md#2"] == "v2"
    assert by_id["wiki/2026-05#wiki/big.md#10"] == "v10"
    assert dirty == []


def test_plan_shards_life_append_only_embeds_new_entry():
    # The F2 payoff: appending one dated entry re-embeds only that entry and
    # leaves the prior month's shard byte-identical (clean).
    existing = {"life/health.md#0": _line("life", "2026-04", "life/health.md", 0, "H0", vec="V0")}
    desired = {
        "life/health.md#0": _desired("life", "2026-04", "life/health.md", 0, "H0"),  # unchanged
        "life/health.md#1": _desired("life", "2026-05", "life/health.md", 1, "H1"),  # appended
    }
    new_lines = {"life/health.md#1": _line("life", "2026-05", "life/health.md", 1, "H1", vec="V1")}
    final, dirty, changed, deleted = S.plan_shards(desired, existing, new_lines)

    assert changed == ["life/health.md#1"]      # only the appended entry embedded
    assert deleted == []
    assert dirty == ["life/2026-05"]            # prior month shard stays clean
    assert final["life/2026-04"][0]["vec"] == "V0"

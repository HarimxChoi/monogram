"""Conventional Commits parser — pure, stdlib-only."""
from monogram.commit_parse import parse_commit


def test_feat_with_scope():
    p = parse_commit("feat(api): add reindex command")
    assert p.type == "feat" and p.scope == "api"
    assert p.description == "add reindex command"
    assert p.breaking is False and p.is_conventional


def test_breaking_via_bang():
    p = parse_commit("feat!: drop python 3.9")
    assert p.type == "feat" and p.breaking is True


def test_breaking_via_footer():
    p = parse_commit("refactor: rework store\n\nBREAKING CHANGE: api moved")
    assert p.type == "refactor" and p.breaking is True


def test_non_conventional_degrades_gracefully():
    p = parse_commit("just fixed the thing")
    assert p.type is None and p.is_conventional is False
    assert p.description == "just fixed the thing"


def test_issue_refs_deduped_and_ordered():
    p = parse_commit("fix: bug\n\nCloses #12, also refs #5 and #12 again")
    assert p.issues == ["#12", "#5"]


def test_co_authors():
    p = parse_commit("feat: x\n\nCo-authored-by: Ada <ada@x.io>\nCo-authored-by: Bo <bo@x.io>")
    assert p.co_authors == ["Ada <ada@x.io>", "Bo <bo@x.io>"]


def test_empty_message():
    p = parse_commit("")
    assert p.type is None and p.description == "" and p.issues == []

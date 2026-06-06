"""Single-instance guard ownership-logic tests (pure, no network)."""
from monogram.instance_lock import _is_owner


def test_is_owner():
    assert _is_owner({"id": "abc123"}, "abc123") is True
    assert _is_owner({"id": "abc123"}, "other") is False
    assert _is_owner(None, "abc123") is False          # no lock yet
    assert _is_owner({}, "abc123") is False             # malformed lock
    assert _is_owner({"id": ""}, "abc123") is False

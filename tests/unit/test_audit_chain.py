"""Hash-chaining for the audit trail — pure, no database."""

from __future__ import annotations

import json

from msa_lims.domain.audit_chain import (
    GENESIS_PREV_HASH,
    canonical_entry,
    compute_entry_hash,
)


def _entry(**overrides: object) -> dict[str, object]:
    defaults: dict[str, object] = {
        "prev_entry_hash": None,
        "table_name": "client",
        "record_id": 1,
        "action": "create",
        "before": None,
        "after": {"name": "Acme Mining"},
        "reason": None,
        "actor_id": 7,
        "actor_ip": None,
    }
    defaults.update(overrides)
    return defaults


class TestComputeEntryHash:
    def test_is_a_64_character_hex_string(self) -> None:
        digest = compute_entry_hash(**_entry())
        assert len(digest) == 64
        assert all(c in "0123456789abcdef" for c in digest)

    def test_is_deterministic_for_identical_input(self) -> None:
        assert compute_entry_hash(**_entry()) == compute_entry_hash(**_entry())

    def test_a_genesis_row_and_an_explicit_genesis_sentinel_hash_identically(self) -> None:
        """`prev_entry_hash=None` (what the very first row in the table
        actually has) must hash exactly the way an explicit
        `GENESIS_PREV_HASH` would — the sentinel substitution is internal to
        the hash, not a second, different convention."""
        via_none = compute_entry_hash(**_entry(prev_entry_hash=None))
        via_sentinel = compute_entry_hash(**_entry(prev_entry_hash=GENESIS_PREV_HASH))
        assert via_none == via_sentinel

    def test_a_different_prev_hash_changes_the_result(self) -> None:
        a = compute_entry_hash(**_entry(prev_entry_hash="a" * 64))
        b = compute_entry_hash(**_entry(prev_entry_hash="b" * 64))
        assert a != b

    def test_every_field_participates_in_the_hash(self) -> None:
        """Changing any one field — the whole reason a hash chain is worth
        anything — must change the result. Proven for each field in turn
        rather than trusted."""
        base = compute_entry_hash(**_entry())
        variants = [
            _entry(table_name="sample"),
            _entry(record_id=2),
            _entry(action="transition"),
            _entry(before={"status": "received"}),
            _entry(after={"name": "Different Co"}),
            _entry(reason="a stated reason"),
            _entry(actor_id=8),
            _entry(actor_ip="10.0.0.1"),
        ]
        for variant in variants:
            assert compute_entry_hash(**variant) != base

    def test_dict_key_order_does_not_affect_the_hash(self) -> None:
        """`after={"a": 1, "b": 2}` and `after={"b": 2, "a": 1}` are the same
        fact — canonical serialization must treat them identically, or two
        honest writers of the same content could produce different chains."""
        a = compute_entry_hash(**_entry(after={"a": 1, "b": 2}))
        b = compute_entry_hash(**_entry(after={"b": 2, "a": 1}))
        assert a == b


class TestCanonicalEntry:
    def test_is_valid_json(self) -> None:
        entry = canonical_entry(
            prev_entry_hash=GENESIS_PREV_HASH,
            table_name="client",
            record_id=1,
            action="create",
            before=None,
            after={"name": "Acme"},
            reason=None,
            actor_id=1,
            actor_ip=None,
        )
        parsed = json.loads(entry)
        assert parsed["table_name"] == "client"

    def test_has_sorted_keys_and_no_extra_whitespace(self) -> None:
        entry = canonical_entry(
            prev_entry_hash=GENESIS_PREV_HASH,
            table_name="client",
            record_id=1,
            action="create",
            before=None,
            after=None,
            reason=None,
            actor_id=None,
            actor_ip=None,
        )
        assert ", " not in entry
        assert ": " not in entry
        keys = list(json.loads(entry).keys())
        assert keys == sorted(keys)

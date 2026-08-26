"""The one canonical JSON serialization shared by the audit chain (idea #1)
and the provenance dossier's seal (idea #3)."""

from __future__ import annotations

import hashlib
import json

from msa_lims.domain.canonical import canonical_json, canonical_sha256


class TestCanonicalJson:
    def test_dict_key_order_does_not_affect_the_output(self) -> None:
        assert canonical_json({"a": 1, "b": 2}) == canonical_json({"b": 2, "a": 1})

    def test_output_is_valid_json(self) -> None:
        assert json.loads(canonical_json({"x": [1, 2, None, "y"]})) == {"x": [1, 2, None, "y"]}

    def test_has_no_extra_whitespace(self) -> None:
        rendered = canonical_json({"a": 1, "b": [1, 2]})
        assert ", " not in rendered
        assert ": " not in rendered

    def test_nested_structures_are_also_canonicalised(self) -> None:
        a = canonical_json({"outer": {"z": 1, "a": 2}})
        b = canonical_json({"outer": {"a": 2, "z": 1}})
        assert a == b


class TestCanonicalSha256:
    def test_is_a_64_character_hex_digest(self) -> None:
        digest = canonical_sha256({"a": 1})
        assert len(digest) == 64
        assert all(c in "0123456789abcdef" for c in digest)

    def test_matches_hashing_canonical_json_directly(self) -> None:
        payload = {"b": 2, "a": [1, 2, 3]}
        assert (
            canonical_sha256(payload)
            == hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
        )

    def test_key_order_does_not_affect_the_hash(self) -> None:
        assert canonical_sha256({"a": 1, "b": 2}) == canonical_sha256({"b": 2, "a": 1})

    def test_different_payloads_hash_differently(self) -> None:
        assert canonical_sha256({"a": 1}) != canonical_sha256({"a": 2})

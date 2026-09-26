"""Identity resolution tests: deterministic keys, evidence priority, dedupe.

The behaviour under test is what makes discovery idempotent — repeated
discovery of the same resource must resolve to the SAME identity keys so it
updates rather than creating a duplicate.
"""
from __future__ import annotations

from app.services.recognition import enrich_identity
from app.services.recognition.identity import (
    KIND_CLOUD_RESOURCE_ID,
    KIND_HOSTNAME,
    KIND_K8S_UID,
    KIND_MAC,
    KIND_MANAGEMENT_IP,
    KIND_PROVIDER_RESOURCE_ID,
    KIND_SERIAL,
    KIND_VM_UUID,
    STRONG_THRESHOLD,
    IdentityEvidence,
    has_strong_identity,
    identity_confidence,
    identity_keys,
    identity_source,
    normalize_mac,
    same_resource,
    strong_identity_keys,
)


class TestEvidenceStrength:
    def test_evidence_strength_follows_documented_priority(self):
        ordered = [
            IdentityEvidence(KIND_PROVIDER_RESOURCE_ID, "x"),
            IdentityEvidence(KIND_SERIAL, "x"),
            IdentityEvidence(KIND_CLOUD_RESOURCE_ID, "x"),
            IdentityEvidence(KIND_VM_UUID, "x"),
            IdentityEvidence(KIND_K8S_UID, "x"),
            IdentityEvidence(KIND_MANAGEMENT_IP, "x"),
            IdentityEvidence(KIND_MAC, "x"),
            IdentityEvidence(KIND_HOSTNAME, "x"),
        ]
        strengths = [e.strength for e in ordered]
        assert strengths == sorted(strengths, reverse=True)

    def test_only_high_strength_kinds_are_strong(self):
        assert IdentityEvidence(KIND_SERIAL, "x").is_strong
        assert IdentityEvidence(KIND_CLOUD_RESOURCE_ID, "x").is_strong
        assert not IdentityEvidence(KIND_MANAGEMENT_IP, "x").is_strong
        assert not IdentityEvidence(KIND_HOSTNAME, "x").is_strong

    def test_mac_normalization_is_canonical(self):
        assert normalize_mac("AA-BB-CC-DD-EE-FF") == "aa:bb:cc:dd:ee:ff"
        assert normalize_mac("aabb.ccdd.eeff") == "aa:bb:cc:dd:ee:ff"
        assert normalize_mac("not-a-mac") is None
        assert normalize_mac(None) is None


class TestIdentityKeys:
    def test_all_keys_are_returned_strongest_first(self):
        keys = identity_keys({
            "external_id": "vm-123",
            "serial_number": "SN-9",
            "management_ip": "10.0.0.1",
            "hostname": "leaf-1",
        })
        # Every usable signal is present, ordered by evidence priority.
        assert keys == [
            "provider_resource_id:vm-123",
            "serial_number:sn-9",
            "management_ip:10.0.0.1",
            "hostname:leaf-1",
        ]

    def test_weak_signals_survive_a_missing_strong_signal(self):
        """A later poll that cannot read the serial must still match."""
        full = identity_keys({"serial_number": "SN-9", "management_ip": "10.0.0.1"})
        partial = identity_keys({"management_ip": "10.0.0.1"})
        assert set(partial) & set(full)

    def test_strong_subset_excludes_weak_signals(self):
        resource = {"serial_number": "SN-9", "management_ip": "10.0.0.1"}
        assert strong_identity_keys(resource) == ["serial_number:sn-9"]
        assert has_strong_identity(resource) is True
        assert has_strong_identity({"management_ip": "10.0.0.1"}) is False

    def test_hostname_only_resource_still_gets_a_key(self):
        assert identity_keys({"hostname": "VPC1"}) == ["hostname:vpc1"]

    def test_no_evidence_yields_no_keys(self):
        assert identity_keys({}) == []
        assert identity_confidence({}) == 0.0
        assert identity_source({}) is None
        assert has_strong_identity({}) is False

    def test_kind_prefix_prevents_cross_kind_collisions(self):
        a = identity_keys({"serial_number": "10.0.0.1"})
        b = identity_keys({"management_ip": "10.0.0.1"})
        assert a != b

    def test_key_case_normalization_is_stable(self):
        assert identity_keys({"hostname": "Leaf-1"}) == identity_keys(
            {"hostname": "leaf-1"}
        )


class TestIdempotency:
    def test_repeated_discovery_of_same_resource_matches(self):
        first = {"serial_number": "FTX-001", "hostname": "fw-1", "management_ip": "10.0.0.1"}
        second = {"serial_number": "FTX-001", "hostname": "fw-1", "management_ip": "10.0.0.1"}
        assert identity_keys(first) == identity_keys(second)
        assert same_resource(first, second) is True

    def test_ip_change_does_not_break_serial_identity(self):
        before = {"serial_number": "FTX-001", "management_ip": "10.0.0.1"}
        after = {"serial_number": "FTX-001", "management_ip": "10.0.0.9"}
        assert same_resource(before, after) is True

    def test_unrelated_resources_are_never_merged(self):
        a = {"serial_number": "AAA-1", "management_ip": "10.0.0.1"}
        b = {"serial_number": "BBB-2", "management_ip": "10.0.0.2"}
        assert same_resource(a, b) is False

    def test_resource_without_evidence_never_matches_anything(self):
        assert same_resource({}, {"serial_number": "AAA-1"}) is False

    def test_confidence_reflects_strongest_evidence(self):
        assert identity_confidence({"serial_number": "SN-1"}) >= STRONG_THRESHOLD
        assert identity_confidence({"management_ip": "10.0.0.1"}) < STRONG_THRESHOLD

    def test_identity_source_names_the_strongest_evidence(self):
        assert identity_source({
            "hostname": "x", "management_ip": "10.0.0.1", "serial_number": "SN-1",
        }) == KIND_SERIAL

    def test_enrich_identity_attaches_all_three_fields(self):
        enriched = enrich_identity({"serial_number": "SN-1", "hostname": "a"})
        assert enriched["identity_keys"]
        assert enriched["identity_confidence"] >= STRONG_THRESHOLD
        assert enriched["identity_source"] == KIND_SERIAL

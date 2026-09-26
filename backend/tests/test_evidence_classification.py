"""
Focused evidence-classification tests (Phase 9.4 §3, §4, §5, §6, §10 C+D+E).

Two axes that must never be conflated:

    transport       HOW the evidence was obtained (icmp/tcp/snmp/ssh/http/api/agent)
    classification  WHAT kind of interaction produced it (active/passive/api)

These tests pin: the three classifications exist and are the whole set; the
transport -> classification mapping is data and complete for every transport this
platform declares; an unknown transport is NOT guessed; classification is
provenance only (never identity evidence); and merge keeps stronger evidence.
"""
from __future__ import annotations

import pytest

from app.services.discovery import evidence as evidence_module
from app.services.discovery.evidence import (
    DiscoveryEvidence,
    TRANSPORT_AGENT,
    TRANSPORT_API,
    TRANSPORT_HTTP,
    TRANSPORT_ICMP,
    TRANSPORT_SNMP,
    TRANSPORT_SSH,
    TRANSPORT_TCP,
)
from app.services.discovery.service import _probe_evidence
from app.services.infrastructure_intelligence import pipeline
from app.services.infrastructure_intelligence import providers as PROV
from app.services.infrastructure_intelligence import vocabulary as V


class TestClassificationVocabulary:
    """§3/§4 - one canonical vocabulary, three values, no fourth bucket."""

    def test_the_three_classifications_exist(self):
        assert V.EVIDENCE_ACTIVE == "active"
        assert V.EVIDENCE_PASSIVE == "passive"
        assert V.EVIDENCE_API == "api"
        assert set(V.EVIDENCE_CLASSIFICATIONS) == {
            V.EVIDENCE_ACTIVE,
            V.EVIDENCE_PASSIVE,
            V.EVIDENCE_API,
        }
        assert len(V.EVIDENCE_CLASSIFICATIONS) == 3

    def test_api_is_not_the_same_as_active(self):
        assert V.EVIDENCE_API != V.EVIDENCE_ACTIVE

    def test_transport_to_classification_is_explicit_data(self):
        assert V.TRANSPORT_EVIDENCE_CLASSIFICATION == {
            "icmp": V.EVIDENCE_ACTIVE,
            "tcp": V.EVIDENCE_ACTIVE,
            "udp": V.EVIDENCE_ACTIVE,
            "snmp": V.EVIDENCE_ACTIVE,
            "ssh": V.EVIDENCE_ACTIVE,
            "http": V.EVIDENCE_ACTIVE,
            "https": V.EVIDENCE_ACTIVE,
            "wmi": V.EVIDENCE_ACTIVE,
            "winrm": V.EVIDENCE_ACTIVE,
            "api": V.EVIDENCE_API,
            "agent": V.EVIDENCE_PASSIVE,
        }

    def test_an_unknown_transport_is_not_guessed(self):
        """A future passive transport must not be mislabelled as ACTIVE."""
        for unknown in ("zeek", "netflow", "syslog", "sflow"):
            assert V.evidence_classification(unknown) is None
        assert V.evidence_classification(None) is None
        assert V.evidence_classification("") is None

    def test_lookup_is_case_and_whitespace_tolerant(self):
        assert V.evidence_classification(" SNMP ") == V.EVIDENCE_ACTIVE
        assert V.evidence_classification("API") == V.EVIDENCE_API

    def test_every_declared_transport_has_a_classification(self):
        """Adding a TRANSPORT_* constant without classifying it fails here."""
        declared = {
            value
            for name, value in vars(evidence_module).items()
            if name.startswith("TRANSPORT_") and isinstance(value, str)
        }
        assert declared
        for transport in declared:
            assert V.evidence_classification(transport) in V.EVIDENCE_CLASSIFICATIONS


class TestTransportAndClassificationAreSeparateAxes:
    """§10 D - transport stays intact; classification is added alongside it."""

    @pytest.mark.parametrize(
        ("transport", "expected"),
        [
            (TRANSPORT_ICMP, V.EVIDENCE_ACTIVE),
            (TRANSPORT_TCP, V.EVIDENCE_ACTIVE),
            (TRANSPORT_SNMP, V.EVIDENCE_ACTIVE),
            (TRANSPORT_SSH, V.EVIDENCE_ACTIVE),
            (TRANSPORT_HTTP, V.EVIDENCE_ACTIVE),
            (TRANSPORT_API, V.EVIDENCE_API),
            (TRANSPORT_AGENT, V.EVIDENCE_PASSIVE),
        ],
    )
    def test_transport_maps_to_classification(self, transport, expected):
        evidence = DiscoveryEvidence(address="10.0.0.9", transport=transport)
        assert evidence.transport == transport
        assert evidence.classification == expected

    def test_snmp_keeps_its_transport_alongside_its_classification(self):
        evidence = DiscoveryEvidence(address="10.0.0.9", transport=TRANSPORT_SNMP)
        assert evidence.transport == "snmp"
        assert evidence.classification == "active"
        assert evidence.transport != evidence.classification

    def test_explicit_classification_wins_over_derivation(self):
        evidence = DiscoveryEvidence(
            address="10.0.0.9",
            transport=TRANSPORT_ICMP,
            classification=V.EVIDENCE_PASSIVE,
        )
        assert evidence.classification == V.EVIDENCE_PASSIVE

    def test_an_unclassifiable_transport_stays_unclassified(self):
        evidence = DiscoveryEvidence(address="10.0.0.9", transport="zeek")
        assert evidence.classification is None

    def test_a_future_passive_provider_can_declare_itself(self):
        """The model supports PASSIVE before any passive provider exists."""
        evidence = DiscoveryEvidence(
            address="10.0.0.9",
            transport="zeek",
            classification=V.EVIDENCE_PASSIVE,
        )
        assert evidence.classification == V.EVIDENCE_PASSIVE

    def test_an_invalid_classification_is_rejected_loudly(self):
        with pytest.raises(ValueError) as excinfo:
            DiscoveryEvidence(address="10.0.0.9", classification="aggressive")
        assert "aggressive" in str(excinfo.value)

    def test_classification_is_not_identity_evidence(self):
        """Recognition must not depend on how a fact was obtained."""
        evidence = DiscoveryEvidence(
            address="10.0.0.9",
            transport=TRANSPORT_SNMP,
            serial="ABC123",
            classification=V.EVIDENCE_API,
        )
        recognition_input = evidence.recognition_input()
        assert "classification" not in recognition_input
        assert recognition_input["ip"] == "10.0.0.9"
        assert recognition_input["serial_number"] == "ABC123"


class TestEvidenceMergeKeepsTheStrongerObservation:
    """§6/§10 E - a weaker later poll never erases what we already knew."""

    def test_serial_survives_a_later_icmp_only_observation(self):
        snmp = DiscoveryEvidence(
            address="10.0.0.9", transport=TRANSPORT_SNMP, serial="ABC123"
        )
        later = DiscoveryEvidence(address="10.0.0.9", transport=TRANSPORT_ICMP)
        assert later.serial is None
        merged = snmp.merge(later)
        assert merged.serial == "ABC123"

    def test_an_unclassifiable_later_poll_cannot_blank_the_classification(self):
        first = DiscoveryEvidence(address="10.0.0.9", transport=TRANSPORT_SNMP)
        later = DiscoveryEvidence(address="10.0.0.9", transport="zeek")
        assert later.classification is None
        merged = first.merge(later)
        assert merged.classification == V.EVIDENCE_ACTIVE

    def test_a_classified_later_poll_updates_the_classification(self):
        first = DiscoveryEvidence(address="10.0.0.9", transport=TRANSPORT_SNMP)
        later = DiscoveryEvidence(address="10.0.0.9", transport=TRANSPORT_API)
        assert first.merge(later).classification == V.EVIDENCE_API

    def test_merge_output_is_itself_classified(self):
        merged = DiscoveryEvidence(
            address="10.0.0.9", transport=TRANSPORT_API
        ).merge(DiscoveryEvidence(address="10.0.0.9", transport=TRANSPORT_API))
        assert merged.classification == V.EVIDENCE_API
        assert merged.transport == TRANSPORT_API

    def test_merge_still_combines_capabilities_and_neighbour_evidence(self):
        from app.services.discovery.evidence import NeighborEvidence

        first = DiscoveryEvidence(
            address="10.0.0.9", transport=TRANSPORT_SNMP, capabilities=["snmp"]
        )
        later = DiscoveryEvidence(
            address="10.0.0.9",
            transport=TRANSPORT_SSH,
            capabilities=["ssh"],
            neighbors=[NeighborEvidence(address="10.0.0.10", evidence_type="lldp")],
        )
        merged = first.merge(later)
        assert merged.capabilities == ["snmp", "ssh"]
        assert merged.neighbors[0].address == "10.0.0.10"
        assert merged.classification == V.EVIDENCE_ACTIVE


class TestProviderEvidenceClassification:
    """§5 - capability evidence can name the classification of its collector."""

    def test_direct_interrogation_providers_are_active(self):
        for provider in (
            V.PROVIDER_SNMP,
            V.PROVIDER_ICMP,
            V.PROVIDER_SSH,
            V.PROVIDER_WMI,
            V.PROVIDER_WINRM,
        ):
            assert (
                PROV.evidence_classification_for_provider(provider) == V.EVIDENCE_ACTIVE
            ), provider

    def test_api_integrations_are_api(self):
        for provider in (
            V.PROVIDER_PRTG,
            V.PROVIDER_ZABBIX,
            V.PROVIDER_FORTIGATE,
            V.PROVIDER_FORTIMANAGER,
            V.PROVIDER_MERAKI,
            V.PROVIDER_VMWARE,
            V.PROVIDER_KUBERNETES,
            V.PROVIDER_AZURE,
            V.PROVIDER_AWS,
            V.PROVIDER_GCP,
        ):
            assert (
                PROV.evidence_classification_for_provider(provider) == V.EVIDENCE_API
            ), provider

    def test_manual_entry_has_no_classification(self):
        assert PROV.evidence_classification_for_provider(V.PROVIDER_MANUAL) is None

    def test_an_unknown_provider_is_not_guessed(self):
        for unknown in ("nmap", "masscan", "zeek", ""):
            assert PROV.evidence_classification_for_provider(unknown) is None
        assert PROV.evidence_classification_for_provider(None) is None

    def test_every_declared_row_uses_canonical_tokens(self):
        for provider, classification in PROV.PROVIDER_EVIDENCE_CLASSIFICATION.items():
            assert provider in V.KNOWN_PROVIDERS, provider
            assert classification in V.EVIDENCE_CLASSIFICATIONS, provider

    def test_the_provider_kind_is_not_used_as_a_proxy(self):
        """FortiGate is API-collected while its kind is 'network'."""
        network_kind = [
            provider
            for provider, kind in PROV.PROVIDER_KINDS.items()
            if kind == PROV.KIND_NETWORK
        ]
        classifications = {
            PROV.evidence_classification_for_provider(provider)
            for provider in network_kind
        }
        assert len(classifications) > 1


class TestCapabilityEvidenceClassification:
    """§5 - classification is ADDED to capability evidence, never replacing it."""

    def test_classification_is_added_without_replacing_provider_evidence(self):
        payload = pipeline.capability_evidence(
            {"collected": True, "sensor": "ping"}, "health", {"health"}, "api"
        )
        assert payload == {
            "collected": True,
            "sensor": "ping",
            "classification": "api",
        }

    def test_an_unclassified_provider_leaves_the_payload_untouched(self):
        payload = pipeline.capability_evidence(None, "cpu", set(), None)
        assert payload == {"collected": False}
        assert "classification" not in payload

    def test_default_payload_reports_whether_the_capability_was_collected(self):
        assert pipeline.capability_evidence(None, "cpu", {"cpu"}, "active") == {
            "collected": True,
            "classification": "active",
        }

    def test_the_assertion_evidence_is_not_mutated(self):
        source = {"collected": True}
        pipeline.capability_evidence(source, "cpu", {"cpu"}, "api")
        assert source == {"collected": True}


class TestDiscoveryEvidenceSurfacesTheClassification:
    """§5/§7 - the classification reaches the persisted evidence payload."""

    def test_probe_evidence_carries_the_classification(self):
        payload = _probe_evidence(
            DiscoveryEvidence(
                address="10.0.0.9", transport=TRANSPORT_SNMP, serial="ABC123"
            )
        )
        assert payload["transport"] == "snmp"
        assert payload["classification"] == V.EVIDENCE_ACTIVE
        assert payload["serial"] == "ABC123"

    def test_probe_evidence_leaves_an_unclassifiable_transport_unclassified(self):
        payload = _probe_evidence(
            DiscoveryEvidence(address="10.0.0.9", transport="zeek")
        )
        assert payload["classification"] is None

    def test_classification_and_capability_namespaces_stay_separate(self):
        """``api`` is both a transport and a capability token - not a classification."""
        evidence = DiscoveryEvidence(
            address="10.0.0.9", transport=TRANSPORT_API, capabilities=["api"]
        )
        payload = _probe_evidence(evidence)
        assert payload["transport"] == "api"
        assert payload["classification"] == V.EVIDENCE_API
        assert payload["capabilities"] == ["api"]

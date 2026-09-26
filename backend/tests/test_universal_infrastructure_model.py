"""
Universal infrastructure MODELS + CONTRACTS (pure, no database).

Covers spec sections 4-6, 15-21, 25, 26, 28, 30, 38:
  * canonical vocabulary normalisation (types, states, BGP, VPN, metrics)
  * capability resolution incl. the mandatory `supported=false` + reason shape
  * provider capability registry wired-vs-declared (against the REAL registry)
  * universal resource projection from a stored Device row
  * canonical telemetry contract (identity/value/quality/context, not_available)
  * universal interface / WAN / SD-WAN / VPN / routing / BGP projections
  * HSI-GNCE graph grouping + root-cause ranking
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.collectors.base import (
    NormalizedBundle,
    NormalizedDevice,
    NormalizedInterface,
    NormalizedMetric,
    NormalizedNeighbor,
)
from app.services.infrastructure_intelligence import capabilities as CAP
from app.services.infrastructure_intelligence import network as NET
from app.services.infrastructure_intelligence import providers as PROV
from app.services.infrastructure_intelligence import telemetry as T
from app.services.infrastructure_intelligence import vocabulary as V
from app.services.infrastructure_intelligence.health import (
    HealthService,
    base_health_for,
    bucket_for_score,
    dependency_penalty,
)
from app.services.infrastructure_intelligence.hsignce import (
    connected_components,
    rank_root_causes,
)
from app.services.infrastructure_intelligence.pipeline import (
    capability_of_metric,
    normalize_bundle,
)
from app.services.infrastructure_intelligence.resources import (
    UniversalResource,
    resource_type_catalog,
)

TENANT = uuid.UUID("11111111-1111-1111-1111-111111111111")


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------
class TestVocabulary:
    @pytest.mark.parametrize(
        "token,expected",
        [
            ("wireless_ap", "access_point"),
            ("FortiGate", "unknown"),          # a VENDOR is not a TYPE
            ("ngfw", "firewall"),
            ("ec2_instance", "cloud_resource"),
            ("aws_account", "cloud_account"),
            ("microsoft.compute/virtualmachines", "virtual_machine"),
            ("k8s_pod", "unknown"),            # not in the universal set
            ("", "unknown"),
            (None, "unknown"),
        ],
    )
    def test_universal_resource_type_never_guesses(self, token, expected):
        assert V.universal_resource_type(token) == expected

    def test_families_are_the_documented_set(self):
        assert V.resource_family("firewall") == V.FAMILY_SECURITY
        assert V.resource_family("access_point") == V.FAMILY_WIRELESS
        assert V.resource_family("cloud_storage") == V.FAMILY_STORAGE
        assert set(V.RESOURCE_FAMILIES) >= {"network", "cloud", "endpoint", "security"}

    def test_operational_state_accepts_every_stored_spelling(self):
        for raw in ("up", "online", "active", "reachable"):
            assert V.operational_state(raw) == V.OPER_STATE_UP
        for raw in ("down", "offline", "critical", "error"):
            assert V.operational_state(raw) == V.OPER_STATE_DOWN
        for raw in ("warning", "degraded", "maintenance"):
            assert V.operational_state(raw) == V.OPER_STATE_DEGRADED
        assert V.operational_state("nonsense") == V.OPER_STATE_UNKNOWN
        assert V.operational_state(None) == V.OPER_STATE_UNKNOWN

    def test_health_from_state_keeps_unknown(self):
        assert V.health_from_oper_state(V.OPER_STATE_UNKNOWN) == V.HEALTH_UNKNOWN

    def test_bgp_and_vpn_normalisation(self):
        assert V.normalize_bgp_state("OpenSent") == V.BGP_STATE_OPEN_SENT
        assert V.normalize_bgp_state("open_confirm") == V.BGP_STATE_OPEN_CONFIRM
        assert V.normalize_bgp_state("ESTAB") == V.BGP_STATE_ESTABLISHED
        assert V.normalize_bgp_state("weird") == V.BGP_STATE_UNKNOWN
        assert NET.normalize_vpn_type("IPsec") == V.VPN_TYPE_IPSEC
        assert NET.normalize_vpn_type("site-to-site") == V.VPN_TYPE_SITE_TO_SITE
        assert NET.normalize_vpn_type("unknown-thing") == V.VPN_TYPE_OTHER

    def test_every_reason_code_is_declared(self):
        assert V.REASON_PROVIDER_CAPABILITY_NOT_SUPPORTED in V.REASONS
        assert V.COLLECTION_NOT_AVAILABLE in V.COLLECTION_STATES
        assert V.SUPPORT_UNKNOWN != V.SUPPORT_UNSUPPORTED


# ---------------------------------------------------------------------------
# Capabilities (spec sections 5, 30, 38)
# ---------------------------------------------------------------------------
class TestCapabilityResolution:
    def test_unsupported_requires_a_reason(self):
        with pytest.raises(ValueError):
            CAP.unsupported("cpu", "")

    def test_unsupported_is_serialised_as_supported_false(self):
        payload = CAP.unsupported(
            "wireless", V.REASON_PROVIDER_CAPABILITY_NOT_SUPPORTED
        ).to_dict()
        assert payload["supported"] is False
        assert payload["support"] == V.SUPPORT_UNSUPPORTED
        assert payload["reason"] == V.REASON_PROVIDER_CAPABILITY_NOT_SUPPORTED

    def test_unknown_is_not_supported_but_still_distinguishable(self):
        payload = CAP.unknown_capability("cpu", V.REASON_NOT_YET_COLLECTED).to_dict()
        assert payload["supported"] is False
        assert payload["support"] == V.SUPPORT_UNKNOWN

    def test_collected_capability_wins_over_provider_profile(self):
        report = CAP.resolve_capabilities(
            "switch", "snmp",
            provider_capabilities=frozenset({V.CAP_INTERFACES}),
            collected={V.CAP_VLAN},
        )
        assert report.supports(V.CAP_VLAN)
        assert report.assertion(V.CAP_VLAN).status == V.COLLECTION_COLLECTED
        vlan = report.assertion(V.CAP_VLAN).to_dict()
        assert vlan["source"] == CAP.SOURCE_COLLECTOR

    def test_capability_the_provider_cannot_supply_is_unsupported_with_reason(self):
        report = CAP.resolve_capabilities(
            "switch", "icmp",
            provider_capabilities=frozenset({V.CAP_AVAILABILITY}),
        )
        assertion = report.assertion(V.CAP_VLAN)
        assert assertion.support == V.SUPPORT_UNSUPPORTED
        assert assertion.reason == V.REASON_PROVIDER_CAPABILITY_NOT_SUPPORTED

    def test_capability_the_type_does_not_expose_is_reported_separately(self):
        report = CAP.resolve_capabilities(
            "endpoint", "prtg",
            provider_capabilities=frozenset({V.CAP_BGP}),
        )
        assertion = report.assertion(V.CAP_BGP)
        assert assertion.reason == V.REASON_RESOURCE_TYPE_DOES_NOT_EXPOSE

    def test_unwired_provider_is_unknown_not_unsupported(self):
        report = CAP.resolve_capabilities("firewall", "paloalto")
        assertion = report.assertion(V.CAP_INTERFACES)
        assert assertion.support == V.SUPPORT_UNKNOWN

    def test_resource_without_a_provider_is_unknown_with_source_reason(self):
        report = CAP.resolve_capabilities("server", None)
        assertion = report.assertion(V.CAP_CPU)
        assert assertion.reason == V.REASON_NO_MONITORING_SOURCE

    def test_explicit_assertion_overrides_everything(self):
        report = CAP.resolve_capabilities(
            "server", "prtg",
            provider_capabilities=frozenset({V.CAP_CPU}),
            asserted={
                V.CAP_TEMPERATURE: CAP.unsupported(
                    V.CAP_TEMPERATURE, V.REASON_RESOURCE_TYPE_DOES_NOT_EXPOSE
                )
            },
        )
        assert (
            report.assertion(V.CAP_TEMPERATURE).reason
            == V.REASON_RESOURCE_TYPE_DOES_NOT_EXPOSE
        )

    def test_unknown_type_exposes_only_the_universal_capabilities(self):
        exposed = set(CAP.type_exposes("not_a_real_type"))
        assert exposed == set(V.UNIVERSAL_CAPABILITIES)

    def test_report_counts_reconcile(self):
        report = CAP.resolve_capabilities(
            "firewall", "fortigate",
            provider_capabilities=frozenset({V.CAP_INTERFACES, V.CAP_VPN}),
            collected={V.CAP_INTERFACES},
        )
        data = report.to_dict()
        assert data["counts"][V.SUPPORT_SUPPORTED] == len(data["supported"])
        assert data["counts"][V.SUPPORT_UNSUPPORTED] == len(data["unsupported"])
        assert data["counts"][V.SUPPORT_UNKNOWN] == len(data["unknown"])


# ---------------------------------------------------------------------------
# Provider registry (spec sections 29, 30)
# ---------------------------------------------------------------------------
class TestProviderRegistry:
    def test_wired_state_comes_from_the_real_collector_registry(self):
        wired = PROV.wired_source_types()
        assert "prtg" in wired and "snmp" in wired
        assert "aruba_central" not in wired

    def test_wired_provider_exposes_capabilities(self):
        caps = PROV.capabilities_for_provider("snmp")
        assert caps and V.CAP_INTERFACES in caps

    def test_unwired_provider_returns_none_not_an_empty_claim(self):
        assert PROV.capabilities_for_provider("aruba_central") is None
        assert PROV.capabilities_for_provider("paloalto") is None
        assert PROV.capabilities_for_provider(None) is None

    def test_catalog_separates_declared_from_wired(self):
        rows = {row["provider"]: row for row in PROV.provider_catalog()}
        assert rows["prtg"]["wired"] is True
        assert rows["aruba_central"]["wired"] is False
        assert rows["aruba_central"]["declared"] is True

    def test_source_type_mapping_is_total_for_collectors(self):
        for source_type in PROV.wired_source_types():
            assert PROV.provider_from_source_type(source_type) is not None, source_type


# ---------------------------------------------------------------------------
# Canonical telemetry contract (spec sections 16, 33, 38)
# ---------------------------------------------------------------------------
class TestTelemetryContract:
    def test_not_available_matches_the_required_shape(self):
        payload = T.not_available("vpn", V.REASON_PROVIDER_CAPABILITY_NOT_SUPPORTED)
        assert payload == {
            "capability": "vpn",
            "supported": False,
            "status": "not_available",
            "reason": "provider_capability_not_supported",
        }

    def test_metric_name_normalisation_never_drops_a_series(self):
        assert T.canonical_metric_name("CPU") == "cpu_utilization_pct"
        assert T.canonical_metric_name("Response Time") == "latency_ms"
        assert (
            T.canonical_metric_name("vendor_specific_thing") == "vendor_specific_thing"
        )
        assert T.canonical_metric_name(None) == "unknown"

    def test_metric_to_capability_lookup(self):
        assert T.capability_for_metric("cpu_utilization_pct") == V.CAP_CPU
        assert T.capability_for_metric("tunnels_up") == V.CAP_VPN
        assert T.capability_for_metric("not_a_metric") is None

    def test_freshness_and_quality_buckets(self):
        now = datetime.now(timezone.utc)
        assert T.freshness_seconds(None) is None
        assert T.quality_for(now, now=now) == T.QUALITY_GOOD
        assert T.quality_for(now - timedelta(seconds=700), now=now) == T.QUALITY_DEGRADED
        assert T.quality_for(now - timedelta(days=1), now=now) == T.QUALITY_STALE
        assert T.quality_for(None) == T.QUALITY_UNKNOWN

    def test_record_round_trips_every_contract_section(self):
        record = T.CanonicalTelemetry(
            tenant_id=TENANT,
            resource_id=uuid.uuid4(),
            provider="prtg",
            capability=V.CAP_CPU,
            metric_name="cpu_utilization_pct",
            timestamp=datetime.now(timezone.utc),
            numeric_value=42.5,
            unit="%",
            dimensions={"node": "core-1"},
        )
        payload = record.to_dict()
        assert payload["capability"] == "cpu"
        assert payload["numeric_value"] == 42.5
        assert payload["dimensions"] == {"node": "core-1"}
        assert payload["has_value"] is True

    def test_boolean_only_record_has_a_value_but_no_number(self):
        record = T.CanonicalTelemetry(
            capability=V.CAP_AVAILABILITY,
            metric_name=T.M_REACHABILITY,
            timestamp=datetime.now(timezone.utc),
            boolean_value=False,
            text_value="down",
        )
        assert record.has_value is True
        assert record.to_dict()["numeric_value"] is None

    def test_summarize_series_is_order_independent_and_honest_when_empty(self):
        now = datetime.now(timezone.utc)
        records = [
            T.CanonicalTelemetry(
                capability=V.CAP_CPU,
                metric_name="cpu_utilization_pct",
                timestamp=now - timedelta(minutes=10),
                numeric_value=10.0,
                unit="%",
            ),
            T.CanonicalTelemetry(
                capability=V.CAP_CPU,
                metric_name="cpu_utilization_pct",
                timestamp=now,
                numeric_value=30.0,
                unit="%",
            ),
        ]
        summary = T.summarize_series(list(reversed(records)), now=now)
        assert summary.samples == 2
        assert summary.minimum == 10.0 and summary.maximum == 30.0
        assert summary.average == 20.0
        assert summary.latest_value == 30.0

        empty = T.summarize_series([], now=now)
        assert empty.samples == 0
        assert empty.average is None and empty.latest_value is None
        assert empty.quality == T.QUALITY_UNKNOWN


# ---------------------------------------------------------------------------
# Universal resource projection (spec sections 4, 6)
# ---------------------------------------------------------------------------
def _device(**overrides):
    """A stored-Device-shaped row. A TEST fixture, not product data."""
    fields = {
        "id": uuid.uuid4(),
        "tenant_id": TENANT,
        "site_id": uuid.uuid4(),
        "zone_id": None,
        "parent_resource_id": None,
        "monitoring_source_id": uuid.uuid4(),
        "external_id": "objid-123",
        "name": "edge-fw-01",
        "device_type": "firewall",
        "vendor": "Fortinet",
        "model": "FG-60F",
        "os_version": "7.4.1",
        "hostname": "edge-fw-01",
        "ip_address": "10.0.0.1",
        "mac_address": None,
        "status": "online",
        "serial_number": "FGT60F0001",
        "criticality": "high",
        "business_service_id": None,
        "role": "firewall_edge",
        "role_class": "security",
        "country": "AE",
        "city": "Dubai",
        "campus": None,
        "building": None,
        "datacenter_name": "DXB1",
        "rack": "R12",
        "provider": "fortigate",
        "lifecycle_state": "active",
        "health": "warning",
        "discovered_at": datetime.now(timezone.utc),
        "last_seen_at": datetime.now(timezone.utc),
        "last_telemetry_at": datetime.now(timezone.utc),
        "custom_metadata": {"description": "Site edge firewall"},
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


class TestUniversalResource:
    def test_projection_uses_the_shared_columns(self):
        device = _device()
        resource = UniversalResource.from_device(device)
        assert resource.resource_type == "firewall"
        assert resource.family == V.FAMILY_SECURITY
        assert resource.provider_resource_id == "objid-123"
        assert resource.status == V.OPER_STATE_UP
        assert resource.status_raw == "online"
        assert resource.health == V.HEALTH_WARNING
        assert resource.health_reason is None
        assert resource.location["datacenter"] == "DXB1"

    def test_caller_supplied_provider_wins_over_the_column(self):
        resource = UniversalResource.from_device(_device(provider=None), provider="prtg")
        assert resource.provider == "prtg"

    def test_missing_health_is_unknown_and_says_why(self):
        resource = UniversalResource.from_device(_device(health=None))
        assert resource.health == V.HEALTH_UNKNOWN
        assert resource.health_reason == V.REASON_NOT_YET_COLLECTED

    def test_parent_resource_is_exposed_for_hierarchy(self):
        parent = uuid.uuid4()
        resource = UniversalResource.from_device(_device(parent_resource_id=parent))
        assert resource.parent_resource_id == parent
        assert resource.to_dict()["parent_resource_id"] == str(parent)

    def test_catalog_covers_every_family_and_type(self):
        catalog = resource_type_catalog()
        families = {entry["family"] for entry in catalog}
        listed = {
            entry["resource_type"]
            for family in catalog
            for entry in family["resource_types"]
        }
        assert listed == set(V.UNIVERSAL_RESOURCE_TYPES)
        assert "cloud" in families and "endpoint" in families


# ---------------------------------------------------------------------------
# Universal network models (spec sections 15, 18, 19, 20, 21)
# ---------------------------------------------------------------------------
def _interface(**overrides):
    fields = {
        "id": uuid.uuid4(),
        "tenant_id": TENANT,
        "device_id": uuid.uuid4(),
        "external_id": "1",
        "name": "wan1",
        "interface_type": "wan",
        "description": "Primary WAN",
        "mac_address": "aa:bb:cc:dd:ee:ff",
        "admin_status": "up",
        "oper_status": "up",
        "speed_mbps": 1000.0,
        "speed_bps": None,
        "in_octets": 10.0,
        "out_octets": 20.0,
        "in_errors": 0.0,
        "out_errors": 1.0,
        "discards_in": None,
        "discards_out": None,
        "rx_bps": 1234.0,
        "tx_bps": None,
        "utilization_pct": None,
        "packet_loss_pct": None,
        "latency_ms": None,
        "jitter_ms": None,
        "vlan_id": None,
        "last_status_change_at": datetime.now(timezone.utc),
        "last_telemetry_at": datetime.now(timezone.utc),
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def _link(**overrides):
    fields = {
        "id": uuid.uuid4(),
        "tenant_id": TENANT,
        "device_a_id": uuid.uuid4(),
        "device_b_id": uuid.uuid4(),
        "source_interface_id": None,
        "dest_interface_id": None,
        "name": "WAN1",
        "link_type": "wan",
        "provider": "Etisalat",
        "circuit_id": "CIR-1",
        "bandwidth_mbps": 500.0,
        "latency_ms": 12.0,
        "packet_loss_pct": 0.1,
        "utilization_pct": 33.0,
        "admin_status": "up",
        "oper_status": "up",
        "redundancy_group": None,
        "vpn_name": None,
        "vpn_type": None,
        "last_state_change_at": datetime.now(timezone.utc),
        "attributes": {},
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


class TestUniversalNetworkModels:
    def test_interface_reports_which_metrics_were_actually_measured(self):
        payload = NET.UniversalInterface.from_interface(_interface()).to_dict()
        assert payload["oper_state"] == V.OPER_STATE_UP
        assert "rx_bps" in payload["measured"]
        assert "tx_bps" not in payload["measured"]
        assert payload["negotiated_speed_mbps"] == 1000.0
        assert payload["speed_bps"] is None  # never silently converted

    def test_wan_role_is_declared_not_invented(self):
        assert NET.WanMember.from_link(_link()).role == V.WAN_ROLE_ACTIVE
        standby = NET.WanMember.from_link(_link(redundancy_group="RG1"))
        assert standby.role == V.WAN_ROLE_STANDBY
        declared = NET.WanMember.from_link(_link(attributes={"role": "backup"}))
        assert declared.role == V.WAN_ROLE_BACKUP

    def test_vpn_tunnel_uses_link_vpn_metadata(self):
        tunnel = NET.VpnTunnel.from_link(
            _link(
                link_type="vpn_tunnel",
                vpn_name="HQ-BRANCH",
                vpn_type="ipsec",
                attributes={"remote_gateway": "203.0.113.5", "uptime_seconds": 3600},
            )
        )
        assert tunnel.vpn_type == V.VPN_TYPE_IPSEC
        assert tunnel.name == "HQ-BRANCH"
        assert tunnel.remote_endpoint == "203.0.113.5"
        assert tunnel.uptime_seconds == 3600.0

    def test_route_projection_preserves_evidence(self):
        route = SimpleNamespace(
            id=uuid.uuid4(), tenant_id=TENANT, resource_id=uuid.uuid4(),
            destination="10.10.0.0/16", protocol="ospf", vrf="default",
            prefix_length=16, next_hop="10.0.0.2", gateway=None, interface_id=None,
            outgoing_interface="port1", metric=20, administrative_distance=110,
            priority=None, state="active", is_best=True, learned=True,
            advertised=False, age_seconds=42.0, first_seen_at=None,
            last_seen_at=None, last_change_at=None, provider="fortigate",
        )
        entry = NET.RouteEntry.from_route(route).to_dict()
        assert entry["protocol"] == "ospf"
        assert entry["is_best"] is True
        assert entry["administrative_distance"] == 110

    def test_bgp_projection_normalises_the_fsm_state(self):
        neighbor = SimpleNamespace(
            id=uuid.uuid4(), tenant_id=TENANT, resource_id=uuid.uuid4(),
            neighbor_ip="10.0.0.2", vrf="-", local_ip="10.0.0.1",
            remote_as="65001", local_as="65000", description=None,
            state="Established", admin_status="enabled", uptime_seconds=100.0,
            prefixes_received=120, prefixes_advertised=8,
            last_state_change_at=None, last_seen_at=None, provider="snmp",
        )
        state = NET.BgpNeighborState.from_neighbor(neighbor)
        assert state.state == V.BGP_STATE_ESTABLISHED
        assert state.is_established is True

    def test_sdwan_scoring_excludes_unmeasured_members_with_a_reason(self):
        measured = NET.WanMember.from_link(
            _link(name="WAN1", latency_ms=20.0, packet_loss_pct=0.2)
        )
        unmeasured = NET.WanMember.from_link(
            _link(name="WAN2", latency_ms=None, packet_loss_pct=None)
        )
        result = NET.evaluate_sdwan_members([measured, unmeasured])
        assert result["available"] is True
        assert result["evaluation"]["selected_path"] == "WAN1"
        assert [entry["name"] for entry in result["not_evaluated"]] == ["WAN2"]
        assert result["not_evaluated"][0]["reason"] == V.REASON_NOT_YET_COLLECTED

    def test_sdwan_with_no_measured_member_is_not_available(self):
        member = NET.WanMember.from_link(
            _link(latency_ms=None, packet_loss_pct=None)
        )
        result = NET.evaluate_sdwan_members([member])
        assert result["available"] is False
        assert result["supported"] is False
        assert result["status"] == V.COLLECTION_NOT_AVAILABLE
        assert result["reason"] == V.REASON_NO_TELEMETRY_EVER
        assert result["not_evaluated"][0]["missing_metrics"] == [
            "latency_ms", "packet_loss_pct"
        ]


# ---------------------------------------------------------------------------
# Normalization pipeline (spec sections 3, 16, 29, 30, 40)
# ---------------------------------------------------------------------------
def _bundle():
    return NormalizedBundle(
        devices=[
            NormalizedDevice(
                external_id="dev-1",
                name="core-sw-1",
                device_type="switch",
                status="up",
            )
        ],
        interfaces=[
            NormalizedInterface(
                device_external_id="dev-1",
                external_id="1",
                name="gi0/1",
                oper_status="up",
            )
        ],
        metrics=[
            NormalizedMetric(
                metric_type="CPU",
                value=17.0,
                recorded_at=datetime.now(timezone.utc),
                unit="%",
                device_external_id="dev-1",
            ),
            NormalizedMetric(
                metric_type="totally_unknown_metric",
                value=5.0,
                recorded_at=datetime.now(timezone.utc),
                device_external_id="dev-1",
            ),
        ],
        neighbors=[
            NormalizedNeighbor(
                device_external_id="dev-1",
                remote_address="10.0.0.9",
                evidence_type="lldp",
            )
        ],
    )


class TestNormalizationPipeline:
    def test_normalize_maps_metrics_and_tracks_collected_capabilities(self):
        device_id = uuid.uuid4()
        interface_id = uuid.uuid4()
        result = normalize_bundle(
            _bundle(),
            tenant_id=TENANT,
            provider="snmp",
            device_id_by_external={"dev-1": device_id},
            interface_id_by_key={("dev-1", "1"): interface_id},
        )
        assert len(result.metric_records) == 2
        assert result.metric_records[0].metric_name == "cpu_utilization_pct"
        assert result.metric_records[0].capability == "cpu"
        collected = result.collected_by_device[device_id]
        assert {
            "cpu", "interfaces", "bandwidth", "errors", "topology",
            "inventory", "availability",
        } <= collected
        assert len(result.availability_records) == 1
        assert result.availability_records[0].boolean_value is True

    def test_unmapped_metrics_are_kept_and_reported(self):
        result = normalize_bundle(
            _bundle(),
            tenant_id=TENANT,
            provider="snmp",
            device_id_by_external={"dev-1": uuid.uuid4()},
            interface_id_by_key={},
        )
        unmapped = [
            note for note in result.notes if note["kind"] == "unclassified_metric"
        ]
        assert [note["metric_type"] for note in unmapped] == ["totally_unknown_metric"]
        record = next(
            r for r in result.metric_records
            if r.metric_name == "totally_unknown_metric"
        )
        assert record.capability == "unclassified"
        assert record.numeric_value == 5.0

    def test_missing_provider_capabilities_are_reported_not_fabricated(self):
        result = normalize_bundle(
            NormalizedBundle(
                devices=[
                    NormalizedDevice(
                        external_id="dev-1", name="sw", device_type="switch"
                    )
                ]
            ),
            tenant_id=TENANT,
            provider="snmp",
            device_id_by_external={"dev-1": uuid.uuid4()},
            interface_id_by_key={},
        )
        absent = [
            note for note in result.notes if note["kind"] == "capability_not_collected"
        ]
        assert absent and V.CAP_VLAN in absent[0]["capabilities"]
        assert result.metric_records == []  # nothing invented

    def test_metric_capability_helper(self):
        assert capability_of_metric("cpu_utilization_pct") == "cpu"
        assert capability_of_metric("nope") == ""


# ---------------------------------------------------------------------------
# Health aggregation rules (spec sections 22, 23, 26, 27)
# ---------------------------------------------------------------------------
class TestHealthRules:
    def test_empty_rollup_is_unknown_not_healthy(self):
        assert bucket_for_score(100, members=0) == V.HEALTH_UNKNOWN
        assert bucket_for_score(100, members=1) == V.HEALTH_HEALTHY
        assert bucket_for_score(70, members=1) == V.HEALTH_WARNING
        assert bucket_for_score(10, members=1) == V.HEALTH_CRITICAL

    def test_dependency_penalties_are_ordered(self):
        assert dependency_penalty(V.HEALTH_CRITICAL) > dependency_penalty(
            V.HEALTH_WARNING
        )
        assert dependency_penalty(V.HEALTH_WARNING) > dependency_penalty(
            V.HEALTH_UNKNOWN
        )
        assert dependency_penalty(V.HEALTH_HEALTHY) == 0.0

    def test_base_health_prefers_the_stored_bucket(self):
        now = datetime.now(timezone.utc)
        device = _device(health="critical", status="online", last_telemetry_at=now)
        health, status, freshness = base_health_for(device, now)
        assert health == V.HEALTH_CRITICAL
        assert status == V.OPER_STATE_UP  # availability and health stay separate
        assert freshness == 0.0

    def test_base_health_derives_from_status_when_nothing_was_computed(self):
        device = _device(health=None, status="down", last_telemetry_at=None)
        health, status, freshness = base_health_for(device)
        assert health == V.HEALTH_CRITICAL
        assert status == V.OPER_STATE_DOWN
        assert freshness is None

    def test_unknown_status_stays_unknown(self):
        device = _device(health=None, status="???", last_telemetry_at=None)
        assert base_health_for(device)[0] == V.HEALTH_UNKNOWN

    def test_health_service_is_constructed_from_repositories(self):
        service = HealthService(
            device_repo=None,
            site_repo=None,
            relationship_repo=None,
            maintenance_repo=None,
        )
        assert service.device_repo is None  # dependency injection, no hidden session


# ---------------------------------------------------------------------------
# HSI-GNCE graph correlation (spec sections 26, 28)
# ---------------------------------------------------------------------------
class TestHsignceGrouping:
    def test_connected_components_are_deterministic(self):
        a, b, c, d = (uuid.uuid4() for _ in range(4))
        adjacency = {
            a: {b},
            b: {a, c},
            c: {b},
            d: set(),
        }
        first = connected_components(adjacency)
        second = connected_components(dict(reversed(list(adjacency.items()))))
        assert first == second, "grouping must not depend on dict/insert order"
        assert sorted(len(component) for component in first) == [1, 3]

    def test_root_cause_ranking_prefers_the_worst_health_and_the_hub(self):
        upstream = str(uuid.uuid4())
        leaf = str(uuid.uuid4())
        members = [
            {
                "resource_id": upstream,
                "name": "wan-link",
                "resource_type": "network_device",
                "health": V.HEALTH_CRITICAL,
            },
            {
                "resource_id": leaf,
                "name": "branch-fw",
                "resource_type": "firewall",
                "health": V.HEALTH_WARNING,
            },
        ]
        adjacency = {uuid.UUID(upstream): {uuid.UUID(leaf)}, uuid.UUID(leaf): set()}
        ranked = rank_root_causes(members, adjacency)
        assert ranked[0]["name"] == "wan-link"
        assert ranked[0]["rank"] == 1
        assert ranked[0]["explains_members"] == 1
        assert ranked[1]["rank"] == 2


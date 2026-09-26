"""Built-in provider adapters.

Each adapter is data + a payload mapper. Registering one here is the ONLY step
needed to add a vendor/provider — the recognition engine and the taxonomy are
never edited for a new vendor.

Fingerprints deliberately require evidence that actually distinguishes the
vendor (a sysObjectID prefix, a cloud provider type, a Meraki model prefix) so an
adapter abstains rather than guessing when the signal is absent.
"""
from __future__ import annotations

from typing import Any

from app.services.recognition.providers import (
    CAP_API,
    CAP_GNMI,
    CAP_ICMP,
    CAP_INTERFACES,
    CAP_NETCONF,
    CAP_SNMP,
    CAP_SSH,
    CAP_TELEMETRY,
    CAP_TOPOLOGY,
    ProviderAdapter,
    RecognitionFingerprint,
    register,
)

# Enterprise OID prefixes (sysObjectID) for the vendors we ship adapters for.
FORTINET_ENTERPRISE = "1.3.6.1.4.1.12356"
CISCO_ENTERPRISE = "1.3.6.1.4.1.9"
ARUBA_ENTERPRISE = "1.3.6.1.4.1.14823"


class _NetworkAdapter(ProviderAdapter):
    adapter_kind = "network"
    capabilities = frozenset({
        CAP_ICMP, CAP_SNMP, CAP_SSH, CAP_TELEMETRY, CAP_TOPOLOGY, CAP_INTERFACES,
    })

    def normalize(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Map a vendor payload onto the normalized resource shape."""
        return {
            "resource_type": raw.get("resource_type"),
            "name": raw.get("name") or raw.get("hostname"),
            "hostname": raw.get("hostname") or raw.get("sys_name"),
            "management_ip": raw.get("ip") or raw.get("management_ip"),
            "model": raw.get("model"),
            "version": raw.get("version") or raw.get("os_version"),
            "serial_number": raw.get("serial_number"),
            "mac_address": raw.get("mac_address"),
            "external_id": raw.get("external_id"),
        }


@register
class FortinetAdapter(_NetworkAdapter):
    provider = "fortinet"
    display_name = "Fortinet"
    capabilities = frozenset({
        CAP_ICMP, CAP_SNMP, CAP_SSH, CAP_API, CAP_TELEMETRY, CAP_TOPOLOGY,
        CAP_INTERFACES,
    })
    fingerprints = (
        RecognitionFingerprint(
            provided=("sys_object_id",), weight=60.0,
            any_prefix=(("sys_object_id", (FORTINET_ENTERPRISE,)),),
            note=f"sysObjectID under {FORTINET_ENTERPRISE}",
        ),
        RecognitionFingerprint(
            provided=("sys_descr",), weight=40.0,
            contains=(("sys_descr", "fortinet"),),
            note="sysDescr mentions Fortinet",
        ),
        RecognitionFingerprint(
            provided=("sys_descr",), weight=40.0,
            contains=(("sys_descr", "fortigate"),),
            note="sysDescr mentions FortiGate",
        ),
    )

    def candidate_types(self) -> tuple[str, ...]:
        return ("firewall", "security_appliance")


@register
class CiscoAdapter(_NetworkAdapter):
    provider = "cisco"
    display_name = "Cisco"
    capabilities = frozenset({
        CAP_ICMP, CAP_SNMP, CAP_SSH, CAP_API, CAP_NETCONF, CAP_GNMI,
        CAP_TELEMETRY, CAP_TOPOLOGY, CAP_INTERFACES,
    })
    fingerprints = (
        RecognitionFingerprint(
            provided=("sys_object_id",), weight=55.0,
            any_prefix=(("sys_object_id", (CISCO_ENTERPRISE,)),),
            note=f"sysObjectID under {CISCO_ENTERPRISE}",
        ),
        RecognitionFingerprint(
            provided=("sys_descr",), weight=35.0,
            contains=(("sys_descr", "cisco"),),
            note="sysDescr mentions Cisco",
        ),
    )

    def candidate_types(self) -> tuple[str, ...]:
        return ("router", "switch", "firewall", "access_point", "wireless_controller")


@register
class ArubaAdapter(_NetworkAdapter):
    provider = "aruba"
    display_name = "Aruba"
    capabilities = frozenset({
        CAP_ICMP, CAP_SNMP, CAP_SSH, CAP_API, CAP_TELEMETRY, CAP_TOPOLOGY,
    })
    fingerprints = (
        RecognitionFingerprint(
            provided=("sys_object_id",), weight=50.0,
            any_prefix=(("sys_object_id", (ARUBA_ENTERPRISE,)),),
            note=f"sysObjectID under {ARUBA_ENTERPRISE}",
        ),
        RecognitionFingerprint(
            provided=("sys_descr",), weight=30.0,
            contains=(("sys_descr", "aruba"),),
            note="sysDescr mentions Aruba",
        ),
    )

    def candidate_types(self) -> tuple[str, ...]:
        return ("access_point", "wireless_controller", "switch")


@register
class MerakiAdapter(_NetworkAdapter):
    provider = "meraki"
    display_name = "Cisco Meraki"
    capabilities = frozenset({CAP_API, CAP_TELEMETRY, CAP_TOPOLOGY})
    # Requires an ACTUAL Meraki model code prefix, so a FortiGate model string
    # can never be claimed by this adapter.
    fingerprints = (
        RecognitionFingerprint(
            provided=("model",), weight=45.0,
            any_prefix=(("model", ("mr", "ms", "mx", "mg", "mv", "mt", "cw")),),
            note="Meraki dashboard model code",
        ),
    )

    def candidate_types(self) -> tuple[str, ...]:
        return ("access_point", "switch", "firewall", "sensor")


# ---------------------------------------------------------------------------
# Cloud / virtualization / container adapters
#
# These are the "cloud connector" side of the architecture. They declare how a
# provider's payload maps onto the normalized resource; the discovery
# orchestration (pagination, rate limiting, credentials) lives in the connector
# layer, so a new cloud needs only an adapter + its fingerprints.
# ---------------------------------------------------------------------------


class _CloudAdapter(ProviderAdapter):
    adapter_kind = "cloud"
    capabilities = frozenset({CAP_API, CAP_TELEMETRY, CAP_TOPOLOGY})

    def normalize(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Map a cloud API resource onto the normalized shape."""
        return {
            "resource_type": raw.get("resource_type") or raw.get("type"),
            "name": raw.get("name"),
            "resource_id": raw.get("resource_id") or raw.get("id"),
            "external_id": raw.get("external_id") or raw.get("resource_id") or raw.get("id"),
            "region": raw.get("region") or raw.get("location"),
            "availability_zone": raw.get("availability_zone") or raw.get("zone"),
            "management_ip": raw.get("management_ip"),
            "serial_number": raw.get("serial_number"),
            "metadata": {
                "subscription_id": raw.get("subscription_id"),
                "resource_group": raw.get("resource_group"),
                "account_id": raw.get("account_id"),
                "project_id": raw.get("project_id"),
                "tags": raw.get("tags"),
            },
        }


@register
class AzureAdapter(_CloudAdapter):
    provider = "azure"
    display_name = "Microsoft Azure"
    fingerprints = (
        RecognitionFingerprint(
            provided=("provider",), contains=(("provider", "azure"),),
            weight=90.0, note="payload declares provider=azure",
        ),
    )

    def candidate_types(self) -> tuple[str, ...]:
        return (
            "virtual_machine", "vnet", "subnet", "network_interface",
            "security_group", "cloud_load_balancer", "cloud_firewall",
            "vpn_gateway", "kubernetes_cluster", "object_storage",
            "sql_database", "nosql_database", "app_service",
            "serverless_function", "key_vault", "block_storage",
            "public_ip", "log_analytics_workspace",
        )


@register
class AwsAdapter(_CloudAdapter):
    provider = "aws"
    display_name = "Amazon Web Services"
    fingerprints = (
        RecognitionFingerprint(
            provided=("provider",), contains=(("provider", "aws"),),
            weight=90.0, note="payload declares provider=aws",
        ),
    )

    def candidate_types(self) -> tuple[str, ...]:
        return (
            "ec2_instance", "vpc", "subnet", "route_table", "security_group",
            "network_acl", "internet_gateway", "nat_gateway", "transit_gateway",
            "cloud_load_balancer", "kubernetes_cluster", "object_storage",
            "sql_database", "nosql_database", "serverless_function",
            "cdn_distribution", "dns_zone", "vpn_gateway", "direct_connect",
        )


@register
class GcpAdapter(_CloudAdapter):
    provider = "gcp"
    display_name = "Google Cloud"
    fingerprints = (
        RecognitionFingerprint(
            provided=("provider",), contains=(("provider", "gcp"),),
            weight=90.0, note="payload declares provider=gcp",
        ),
        RecognitionFingerprint(
            provided=("provider",), contains=(("provider", "google"),),
            weight=90.0, note="payload declares provider=google",
        ),
    )

    def candidate_types(self) -> tuple[str, ...]:
        return (
            "virtual_machine", "vpc", "subnet", "kubernetes_cluster",
            "sql_database", "object_storage", "cloud_load_balancer",
            "nat_gateway", "vpn_gateway", "serverless_function",
        )


@register
class VMwareAdapter(_CloudAdapter):
    provider = "vmware"
    display_name = "VMware"
    fingerprints = (
        RecognitionFingerprint(
            provided=("provider",), contains=(("provider", "vmware"),),
            weight=85.0, note="payload declares provider=vmware",
        ),
    )

    def candidate_types(self) -> tuple[str, ...]:
        return (
            "hypervisor", "hypervisor_host", "virtual_machine", "cluster",
            "datastore", "switch", "network_interface",
        )


@register
class KubernetesAdapter(_CloudAdapter):
    provider = "kubernetes"
    display_name = "Kubernetes"
    fingerprints = (
        RecognitionFingerprint(
            provided=("provider",), contains=(("provider", "kubernetes"),),
            weight=85.0, note="payload declares provider=kubernetes",
        ),
        RecognitionFingerprint(
            provided=("provider",), contains=(("provider", "k8s"),),
            weight=85.0, note="payload declares provider=k8s",
        ),
        RecognitionFingerprint(
            provided=("kubernetes_uid",), weight=80.0,
            note="object carries a Kubernetes UID",
        ),
    )

    def candidate_types(self) -> tuple[str, ...]:
        return (
            "kubernetes_cluster", "kubernetes_node", "namespace", "pod",
            "deployment", "replicaset", "daemonset", "statefulset",
            "service", "ingress",
        )


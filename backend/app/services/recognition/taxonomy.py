"""Universal resource taxonomy: categories + extended (cloud/container) types.

This EXTENDS ``app.models.device_types`` rather than replacing it. That module
already owns the canonical ``device_type`` vocabulary and the vendor catalog;
this module adds the two things a universal (network + cloud + virtual +
container + application) platform needs on top:

  1. ``resource_category`` - the coarse bin the dashboard groups by.
  2. Additional canonical ``resource_type`` values for cloud, virtualization and
     container resources that the network-centric list did not carry.

Nothing here invents a resource: an unknown type normalises to ``unknown`` with
category ``unknown`` and stays visible as such.
"""
from __future__ import annotations

from app.models.device_types import (  # re-exported for consumers
    CANONICAL_DEVICE_TYPES,
    UNCLASSIFIED,
    normalize_device_type,
)

# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------

CATEGORY_NETWORK = "network"
CATEGORY_SECURITY = "security"
CATEGORY_WIRELESS = "wireless"
CATEGORY_COMPUTE = "compute"
CATEGORY_ENDPOINT = "endpoint"
CATEGORY_VIRTUALIZATION = "virtualization"
CATEGORY_CONTAINER = "container"
CATEGORY_STORAGE = "storage"
CATEGORY_CLOUD = "cloud"
CATEGORY_DATABASE = "database"
CATEGORY_APPLICATION = "application"
CATEGORY_UNKNOWN = "unknown"

RESOURCE_CATEGORIES: tuple[str, ...] = (
    CATEGORY_NETWORK,
    CATEGORY_SECURITY,
    CATEGORY_WIRELESS,
    CATEGORY_COMPUTE,
    CATEGORY_ENDPOINT,
    CATEGORY_VIRTUALIZATION,
    CATEGORY_CONTAINER,
    CATEGORY_STORAGE,
    CATEGORY_CLOUD,
    CATEGORY_DATABASE,
    CATEGORY_APPLICATION,
    CATEGORY_UNKNOWN,
)

# ---------------------------------------------------------------------------
# Extended resource types (beyond the network-centric canonical list)
# ---------------------------------------------------------------------------

EXTENDED_RESOURCE_TYPES: dict[str, str] = {
    # compute / virtualization
    "virtual_machine": CATEGORY_VIRTUALIZATION,
    "hypervisor_host": CATEGORY_VIRTUALIZATION,
    "datastore": CATEGORY_STORAGE,
    "cluster": CATEGORY_VIRTUALIZATION,
    # containers
    "kubernetes_cluster": CATEGORY_CONTAINER,
    "kubernetes_node": CATEGORY_COMPUTE,
    "namespace": CATEGORY_CONTAINER,
    "pod": CATEGORY_CONTAINER,
    "deployment": CATEGORY_CONTAINER,
    "daemonset": CATEGORY_CONTAINER,
    "statefulset": CATEGORY_CONTAINER,
    "replicaset": CATEGORY_CONTAINER,
    "service": CATEGORY_CONTAINER,
    "ingress": CATEGORY_CONTAINER,
    "container": CATEGORY_CONTAINER,
    "docker_host": CATEGORY_CONTAINER,
    # cloud primitives
    "ec2_instance": CATEGORY_CLOUD,
    "cloud_vm": CATEGORY_CLOUD,
    "vpc": CATEGORY_CLOUD,
    "vnet": CATEGORY_CLOUD,
    "subnet": CATEGORY_CLOUD,
    "network_interface": CATEGORY_CLOUD,
    "security_group": CATEGORY_SECURITY,
    "network_acl": CATEGORY_SECURITY,
    "cloud_firewall": CATEGORY_SECURITY,
    "cloud_load_balancer": CATEGORY_NETWORK,
    "application_gateway": CATEGORY_NETWORK,
    "nat_gateway": CATEGORY_NETWORK,
    "internet_gateway": CATEGORY_NETWORK,
    "transit_gateway": CATEGORY_NETWORK,
    "vpn_gateway": CATEGORY_NETWORK,
    "expressroute_circuit": CATEGORY_NETWORK,
    "direct_connect": CATEGORY_NETWORK,
    "route_table": CATEGORY_CLOUD,
    "public_ip": CATEGORY_CLOUD,
    "dns_zone": CATEGORY_NETWORK,
    "cdn_distribution": CATEGORY_NETWORK,
    "object_storage": CATEGORY_STORAGE,
    "block_storage": CATEGORY_STORAGE,
    "file_storage": CATEGORY_STORAGE,
    "container_registry": CATEGORY_CONTAINER,
    "serverless_function": CATEGORY_COMPUTE,
    "app_service": CATEGORY_APPLICATION,
    "key_vault": CATEGORY_SECURITY,
    "iam_role": CATEGORY_SECURITY,
    "log_analytics_workspace": CATEGORY_APPLICATION,
    # data
    "sql_database": CATEGORY_DATABASE,
    "nosql_database": CATEGORY_DATABASE,
    "cache": CATEGORY_DATABASE,
    # endpoints (managed/user devices, distinct from monitored servers)
    "endpoint": CATEGORY_ENDPOINT,
    "workstation": CATEGORY_ENDPOINT,
    "laptop": CATEGORY_ENDPOINT,
    "mobile_device": CATEGORY_ENDPOINT,
    # applications
    "application": CATEGORY_APPLICATION,
    "web_service": CATEGORY_APPLICATION,
    "database_service": CATEGORY_DATABASE,
}

# Category for every type in the pre-existing network-centric canonical list.
_BASE_TYPE_CATEGORY: dict[str, str] = {
    "router": CATEGORY_NETWORK,
    "switch": CATEGORY_NETWORK,
    "access_point": CATEGORY_WIRELESS,
    "firewall": CATEGORY_SECURITY,
    "security_appliance": CATEGORY_SECURITY,
    "wireless_controller": CATEGORY_WIRELESS,
    "load_balancer": CATEGORY_NETWORK,
    "wan_optimizer": CATEGORY_NETWORK,
    "server": CATEGORY_COMPUTE,
    "sensor": CATEGORY_NETWORK,
    "vm": CATEGORY_VIRTUALIZATION,
    "hypervisor": CATEGORY_VIRTUALIZATION,
    "cloud_resource": CATEGORY_CLOUD,
    "storage": CATEGORY_STORAGE,
    "backup_appliance": CATEGORY_STORAGE,
    "other": CATEGORY_UNKNOWN,
}

ALL_RESOURCE_TYPES: tuple[str, ...] = tuple(
    sorted(set(CANONICAL_DEVICE_TYPES) | set(EXTENDED_RESOURCE_TYPES))
)

# Cloud provider type spellings -> canonical resource_type. Kept data-only so a
# new provider adds mappings here instead of editing recognition logic.
_PROVIDER_TYPE_ALIASES: dict[str, str] = {
    "virtualmachine": "virtual_machine",
    "virtual_machines": "virtual_machine",
    "microsoft.compute/virtualmachines": "virtual_machine",
    "aws_instance": "ec2_instance",
    "instance": "ec2_instance",
    "storage_account": "object_storage",
    "microsoft.storage/storageaccounts": "object_storage",
    "microsoft.network/virtualnetworks": "vnet",
    "microsoft.network/subnets": "subnet",
    "microsoft.network/networksecuritygroups": "security_group",
    "microsoft.network/loadbalancers": "cloud_load_balancer",
    "microsoft.network/azurefirewalls": "cloud_firewall",
    "microsoft.network/publicipaddresses": "public_ip",
    "microsoft.network/virtualnetworkgateways": "vpn_gateway",
    "microsoft.sql/servers/databases": "sql_database",
    "microsoft.web/sites": "app_service",
    "microsoft.keyvault/vaults": "key_vault",
    "microsoft.containerservice/managedclusters": "kubernetes_cluster",
    "microsoft.compute/disks": "block_storage",
    "microsoft.documentdb/databaseaccounts": "nosql_database",
    "k8s_pod": "pod",
    "k8s_node": "kubernetes_node",
    "k8s_service": "service",
    "k8s_deployment": "deployment",
    "k8s_namespace": "namespace",
    "virtualswitch": "switch",
}


def category_for(resource_type: str | None) -> str:
    """Category for a canonical resource_type. An unknown value never guesses."""
    if not resource_type:
        return CATEGORY_UNKNOWN
    token = resource_type.strip().lower()
    if token in EXTENDED_RESOURCE_TYPES:
        return EXTENDED_RESOURCE_TYPES[token]
    if token in _BASE_TYPE_CATEGORY:
        return _BASE_TYPE_CATEGORY[token]
    return CATEGORY_UNKNOWN


def normalize_resource_type(value: str | None) -> str:
    """Normalise any provider-reported type token to a canonical resource_type.

    Accepts both the extended vocabulary and the legacy device vocabulary. An
    unrecognised token becomes ``unknown`` rather than being silently bucketed
    with something it is not.
    """
    if not value:
        return "unknown"
    token = value.strip().lower().replace("-", "_").replace(" ", "_")
    if token in EXTENDED_RESOURCE_TYPES:
        return token
    if token in _PROVIDER_TYPE_ALIASES:
        return _PROVIDER_TYPE_ALIASES[token]
    normalised = normalize_device_type(token)
    if normalised and normalised != UNCLASSIFIED:
        return normalised
    return "unknown"

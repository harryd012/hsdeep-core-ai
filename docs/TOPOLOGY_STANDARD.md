# Topology Standard (Sections 14 & 17)

## Reference topology concept

The uploaded **MPLS VPN OSPF topology** is treated as a *standard enterprise
topology pattern* — never hardcoded IPs, customer names, or labels in
production logic. It models:

- Customer sites
- Customer Edge routers (**CE**)
- Provider Edge routers (**PE**)
- Provider core routers (**P**)
- MPLS provider backbone
- OSPF processes and areas
- VPN instances and VRFs
- Serial / WAN links, LAN subnets
- Site-to-site connectivity across multiple enterprise locations
- Redundant paths
- Routing relationships and device-to-device dependencies
- Service-provider connectivity

## Topology data model

Hierarchy: `Tenant → Region → Country → City → Campus → Building → Data Center
→ Site → Zone → Rack → Device → Interface → Link → Service → Dependency`.

### Device attributes (`devices` table)

Existing: id, hostname, vendor, model, serial_number, device_type, os_version,
ip, mac, site_id, zone_id, status, criticality, business_service_id.

**Added (Sections 13-23):** `role`, `role_class`, `country`, `city`, `campus`,
`building`, `datacenter_name`, `rack`, `custom_metadata`.

`role` uses the canonical `role_class` buckets from
`app/services/topology_standard.py` (`DEVICE_ROLES`): network / security /
compute / storage / wireless / cloud / other — including `ce` / `pe` / `p`.

### Link attributes — new `topology_links` table

`app/models/topology_link.py` carries the full Section 17 link vocabulary:

- Link ID, source/dest device + interfaces
- Link type (wan/lan/serial/mpls/vpn_tunnel/backbone/cloud/loopback/other)
- Bandwidth, latency, packet loss, utilization
- Operational & administrative status, last state change
- Provider, circuit id, external id
- Routing protocols, OSPF (process/area), BGP (ASN/neighbor), VRF list,
  VPN name/type, redundancy group, is_redundant
- Discovery provenance + free-form attributes

Validation of the vocabulary lives in
`app/services/topology_standard.validate_link_attributes()` and
`app/schemas/topology_link.py`.

### Routing & VPN attributes

OSPF process/area, BGP ASN/neighbor, VRF name(s), VPN name/type, MPLS label
information, route status, neighbor state, tunnel state, path availability are
all represented as JSONB fields on `TopologyLink` (and on `ResourceRelationship`
for generic dependency edges).

## Topology engine capabilities supported

- Automatic discovery (collectors) and manual onboarding
- L2 / L3 / WAN / MPLS / VPN / cloud / physical / logical topology
- Dependency topology & service-impact view (existing `TopologyService` BFS)
- Redundant-path analysis & path tracing — **added** in
  `app/services/topology_standard.py` (`trace_path`, `redundant_paths`,
  `path_exists`)
- Root-cause / blast-radius via `TopologyService`
- Device & link health overlays, historical topology changes

## API surface

Existing `/api/topology/relationships*` plus **added** `/api/topology/links*`:

- `POST /api/topology/links` (upsert, idempotent)
- `GET  /api/topology/links` (list + filter by type/status/device)
- `GET  /api/topology/links/summary`
- `GET/PATCH/DELETE /api/topology/links/{id}`

## DB migration

`backend/alembic/versions/f2a1b3c4d5e6_add_topology_enterprise_standard.py`
creates `topology_links` and adds the Device/Site topology + location columns.
All new columns are nullable/additive; existing rows stay unclassified.

## Tests

`tests/test_topology_standard.py` (pure graph + role + validation tests).
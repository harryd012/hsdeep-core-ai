# Firewall gateway

Status: approved 2026-10-30; phase A implemented (batch 11, see
docs/PLATFORM_LEDGER.md). Decisions and differences from the proposal:

- Open decisions taken as proposed: FortiGate and Palo Alto first; a VPN
  tunnel alerts by default only after it has been seen up.
- Alerts reuse the sensor path (readings with `fw:` external ids) instead of
  a new alert type, so they auto-resolve when the firewall recovers.
- No mixin: `BaseCollector` gains `category` and `firewall_capabilities`
  class attributes, and the records live in `app/collectors/base.py`.
- Licences are stored on `firewall_status.licences` (JSONB), not in
  `device.custom_metadata`; tunnels removed from a device are deleted.
- Thresholds are global settings (`FIREWALL_*`) for now; per-tenant
  thresholds are phase B.
- FortiGate's REST API exposes no session limit, so session load alerts
  need PAN-OS (or a later FortiGate source for the limit).
- PAN-OS over SNMP has no configuration-sync object: there `ha_sync`
  reports whether the HA peer is present and healthy.
- The Firewalls page is `/infrastructure/firewalls` with a detail panel
  (tunnels, licences) rather than a drawer.

## Problem

Firewalls are the device class MSP customers ask about first, and the
platform treats them like any other box:

- **Two vendor collectors, no shared contract.** `fortigate.py` reads system
  status, interfaces and IPv4 routes; `paloalto.py` reads system info,
  interfaces and routes. Neither reads anything firewall-specific: HA state,
  VPN tunnels, session table, CPU/memory, licence or signature expiry. Each
  vendor would invent its own field names for these if added ad hoc.
- **Every other firewall brand gets nothing** unless someone writes a full
  collector, even though nearly all of them speak SNMP.
- **The AI Copilot "Tool gateway" panel is hard-coded.**
  `TOOL_GATEWAY_ADAPTERS` in the frontend lists 10 entries, all shown as
  "Registered" whether or not the tenant has connected them. The backend
  registry has 19 collectors (Palo Alto, AWS, GCP, Kubernetes, Datadog,
  connectivity and WMI are missing from the panel) and already exposes them
  at `GET /api/monitoring/source-types`.

There is no single API every firewall speaks, so "standard" here means one
firewall data model and one gateway entry, with per-vendor adapters behind it
and a generic SNMP fallback for everything else.

## Scope

### 1. Firewall data model (`app/collectors/firewall.py`)

New normalized records, added to `NormalizedBundle` as optional lists so no
existing collector changes:

| Record | Fields |
| --- | --- |
| `NormalizedFirewallStatus` (one per device) | `ha_mode` (standalone, active-passive, active-active), `ha_role` (active, passive, primary, secondary), `ha_sync` (in_sync, out_of_sync), `ha_peer_serial`; `session_count`, `session_limit`; `cpu_percent`, `memory_percent`; `firmware_version` |
| `NormalizedVpnTunnel` | `name`, `type` (ipsec, ssl), `remote_gateway`, `status` (up, down), `phase2_up`/`phase2_total`, `bytes_in`/`bytes_out`, `last_change_at` |
| `NormalizedLicence` | `feature` (support, threat signatures, URL filtering, …), `expires_at`, `status` (valid, expiring, expired) |
| `NormalizedPolicyHit` (optional, phase B) | `policy_id`, `name`, `hit_count`, `last_hit_at` |

Rules, matching the rest of the platform:

- **Any field a device does not report is `None`, never a default.** "HA not
  reported" is not "standalone", and a missing session count is not 0.
- `firewall_status` in the bundle carries a per-table outcome (`collected`,
  `empty`, `unavailable`, `failed`), the same vocabulary as `l3_status`. A
  failed VPN read never retires tunnels an earlier poll stored.
- `device_type="firewall"` on the `NormalizedDevice`, unchanged.

### 2. `FirewallCollector` mixin

A small mixin with three optional hooks: `collect_firewall_status()`,
`collect_vpn_tunnels()` and `collect_licences()`. Each is called only if the
adapter implements it. A capability list (`firewall_capabilities`) on the
class says which tables the adapter reads, so the UI can show "not supported
by this adapter" instead of "no data".

### 3. Vendor adapters

| Adapter | Source | Phase |
| --- | --- | --- |
| FortiGate | REST: `monitor/system/ha-peer`, `monitor/system/resource/usage`, `monitor/vpn/ipsec`, `monitor/license/status`, session count from `monitor/system/performance/status` | A |
| Palo Alto PAN-OS | XML op: `show high-availability state`, `show session info`, `show system resources`, `show vpn ipsec-sa`, `request license info` | A |
| Generic SNMP firewall | Standard MIBs (IF-MIB, HOST-RESOURCES-MIB) plus per-vendor OID maps picked by `sysObjectID`: FORTINET-FORTIGATE-MIB, PAN-COMMON-MIB, CISCO-FIREWALL-MIB / CISCO-REMOTE-ACCESS-MONITOR-MIB, CHECKPOINT-MIB, SONICWALL-FIREWALL-IP-STATISTICS-MIB | A (FortiGate and Palo Alto OID maps), B (others) |
| Cisco ASA / FTD, Check Point, SonicWall, Sophos, pfSense/OPNsense native APIs | REST, one collector each | C, on demand |

The generic SNMP adapter extends the existing `snmp` collector, which already
recognises vendors by `sysObjectID` (`recognise_vendor`); it only adds
firewall OID maps. A brand with no map still gets interfaces, CPU and memory
from the standard MIBs, and its HA/VPN tables report `unavailable`.

### 4. Health and alerting

Firewall signals feed the existing health score and the alert/incident path,
using fresh evidence only (`DEVICE_FRESHNESS_WINDOW_SECONDS`):

- HA out of sync or peer missing → critical alert.
- VPN tunnel down that was up on the previous poll → warning alert. A tunnel
  that has never been up is shown but does not alert, so idle dial-up
  tunnels don't page anyone.
- Sessions ≥ 80 % / 95 % of limit, CPU or memory ≥ 85 % / 95 % → warning /
  critical (thresholds per tenant, using the existing threshold settings).
- Licence expiring within 30 days → warning; expired → critical.

### 5. API and storage

- New table `firewall_status` (one row per device, upserted each poll) and
  `vpn_tunnels` (one row per device + tunnel name, retired only after a
  `collected` read). Licences go in `device.custom_metadata["licences"]`.
  One additive migration with a clean downgrade.
- `GET /api/firewalls`: tenant-scoped list with HA, sessions, tunnels up/total
  and evidence age. `GET /api/firewalls/{device_id}` for the detail.
  Permission `devices.read`.
- `GET /api/monitoring/source-types` gains `category` (firewall, network,
  server, cloud, monitoring) and `firewall_capabilities`.

### 6. Frontend

- **Tool gateway panel reads the backend.** `TOOL_GATEWAY_ADAPTERS` is
  removed. The panel lists `source-types` grouped by category, joined with
  the tenant's monitoring sources:
  - **Connected**: a source exists and its last successful poll is fresh.
  - **Configured, stale**: a source exists, but its last poll is old or failed.
  - **Available**: the collector exists but the tenant has no source for it.

  "Firewall" is one group with its vendors under it.
- **A Firewalls page** (Dynatrace-style page kit):
  - one row per firewall with HA state, session load, VPN tunnels up/total,
    licence status and evidence age;
  - a detail drawer with the tunnel list;
  - missing data shows as "No data", never green.

## Out of scope

- **Any write action on a firewall**: policy changes, HA failover, tunnel
  reset, reboot. All adapters stay read-only. A later design can add actions
  through the existing approval workflow and SOP runner.
- **A syslog / SNMP trap receiver.** None exists today. Traps would make HA
  and VPN events near-real-time, but they need a listening service and their
  own design. Until then, detection is at poll interval.
- **Policy analysis**: shadowed or unused rules, rule hygiene.

## Security

- Read-only API calls and SNMP GET/WALK only. No commit, push or config
  endpoints are called, and tests assert this per adapter.
- Credentials keep using the encrypted monitoring source fields. Nothing is
  logged except source id, path and outcome.
- TLS verification stays on by default. A self-signed device needs an explicit
  per-source `verify_ssl=false`, which is shown in the UI.
- SNMPv3 (authPriv) is recommended in the UI. v1/v2c stay supported for
  existing deployments.

## Tests

- Per adapter, using recorded fixtures from real API and MIB responses:
  - normalization of HA, sessions, VPN and licences;
  - `None` for fields the device doesn't return;
  - per-table status when a call fails;
  - no write endpoint is ever called.
- Saver: tunnels are retired only after a `collected` read, and a failed read
  keeps the earlier rows.
- Alerting:
  - an HA out-of-sync device raises one alert;
  - a tunnel that goes down raises a warning; one that was never up doesn't;
  - stale evidence raises nothing and shows as "No data".
- API: tenant isolation on `/api/firewalls`, and the RBAC guard covers the
  new routes.
- Frontend: the gateway panel's three states come from API data, and nothing
  shows "Connected" without a fresh poll.

## Phasing and estimate

- **Phase A, about 3 batches:**
  1. data model, mixin, saver, migration and tests;
  2. FortiGate and Palo Alto adapters, plus the SNMP firewall OID maps for
     those two vendors;
  3. alerts and health, the API, the gateway panel driven by the backend,
     and the Firewalls page.
- **Phase B, about 1 batch:** SNMP OID maps for Cisco, Check Point and
  SonicWall, and policy hit counts for FortiGate and Palo Alto.
- **Phase C:** native API adapters for further vendors, one batch each, when
  a customer needs one.

## Open decisions

1. Phase A covers FortiGate and Palo Alto. Should another vendor be first
   instead, for a pilot customer?
2. Should a VPN tunnel going down alert by default, or only for tunnels a
   tenant marks as critical?

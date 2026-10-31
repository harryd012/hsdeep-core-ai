# L3 topology (Meraki-style routed view)

The Topology page's **Tree (L3)** layout connects devices that hold addresses on
the same subnet, draws a subnet shared by three or more devices as a "pipe",
ends routes to unmonitored next hops at a diamond, and roots the tree where the
default route (0.0.0.0/0) leaves the known network. Hovering a device lists its
interface IPs, subnets, default route, static routes and dynamic-route counts.

Everything on the map comes from data the device itself reported. Nothing is
derived from names, management IPs or reachability.

## Data model

| Table | Owner | Notes |
|---|---|---|
| `interface_addresses` (new, migration `a9d3e7c1f2b4`) | `InterfaceAddressRepository` | One row per address. Unique `(device_id, vrf, address)`; index `(tenant_id, vrf, network)`. `network` is computed by `app/core/ip_addressing.py`, never taken from the vendor. Soft-deleted when a successful poll stops reporting it. |
| `network_routes` (existing) | `NetworkRouteRepository` | Now written by collectors through `sync_for_resource`. Rows that stop being reported are marked `withdrawn` and soft-deleted. |

VRF names that mean the global table (`default`, `main`, `global`, `0`, `vr:default`, ...)
are stored as `-`, so two vendors' default tables group together.

## Collectors

| Collector | Addresses | Routes |
|---|---|---|
| SNMP (`collectors/snmp_l3.py`) | `ipAddressTable` (1.3.6.1.2.1.4.34) + `ipAddrTable` (4.20), merged | `inetCidrRouteTable` (4.24.7) → `ipCidrRouteTable` (4.24.4) → `ipRouteTable` (4.21); first with rows wins. Walks capped at 2000 rows; a capped walk is `partial`. |
| FortiGate | `cmdb/system/interface` `ip` (`"a.b.c.d m.m.m.m"` or split `netmask`) + `secondaryip` | `monitor/router/ipv4` |
| Palo Alto | `show interface all` → `ifnet` `<ip>` / `<addr><member>` | `show routing route` (active, non-host entries) |
| Linux SSH | `ip -j addr show` (skips `lo` and container/hypervisor bridges such as `docker0`) | `ip -j route show` |

Meraki, PRTG, Zabbix and the rest do not report L3 data yet. Meraki
(appliance VLANs/static routes, switch routing interfaces) is planned as phase 2.

### Ingestion semantics (`BundleSaver._save_l3`)

`NormalizedBundle.l3_status` carries one outcome per table: `collected | empty |
partial | unavailable | failed`. A missing key means the collector does not read
that table, and the saver does nothing for it.

* `collected` / `empty`: rows are upserted; stored rows the poll did not report are retired.
* `partial` / `unavailable` / `failed`: rows are upserted, nothing is retired. A failed walk never wipes data.
* Connected and static routes are always kept. Dynamic routes are capped per
  device by `L3_MAX_DYNAMIC_ROUTES_PER_DEVICE` (default 500); `SaveResult.routes_capped` reports the drop.

## Read model: `GET /api/infrastructure/topology/l3?site_id=`

`TopologyL3Service` takes four round trips (site check, devices, addresses,
routes) whatever the tenant size; `build_l3_topology` is a pure function.

* Subnets are grouped by **(site, VRF, network)**. MSP tenants reuse RFC 1918
  ranges at every customer site, so the same `192.168.1.0/24` at two sites is two
  subnets. Devices with no site share one "unsited" scope.
* Host prefixes, loopback, link-local and multicast addresses never connect
  anything; they still appear in the hover details.
* 2 devices on a subnet → direct edge; 3+ → `subnet:` pipe node; 1 → hover only.
* Only static and default routes draw edges. A next hop owned by a known device
  in the same scope links to it (unless a subnet already joins them); otherwise a
  `nexthop:` diamond is created, shared by every device using it.
* `roots` lists default-route exits, most used first.
* `coverage` reports how many devices actually reported addresses/routes, so an
  empty map reads as "not collected" rather than "no subnets".
* Tenant comes from the JWT (`get_tenant_id`); a `site_id` outside the tenant is
  a 404; next-hop resolution never crosses tenants.

Device nodes use the live graph's `device:<uuid>` ids and carry no state: the
frontend joins them onto `/topology/graph/live`, so device health has one source.

## Frontend

* `lib/topology/l3.ts` (pure, tested in `tests/topology/l3.test.ts`) maps the
  response to tree input, drops links to devices hidden by filters, and builds
  the hover rows.
* `tree.ts` takes an optional `rootOrder`; without it L2 behaviour is unchanged.
* `TopologyTreeCanvas` draws `subnet` (pipe) and `next-hop` (hollow diamond) glyphs,
  shows addresses where L2 shows port names, and accepts extra hover rows.
* The L3 query only runs while the L3 layout is selected.

## Tests

* `backend/tests/test_l3_parsers.py`: address normalisation and every collector parser.
* `backend/tests/test_topology_l3.py`: pure builder (pipes, diamonds, rooting,
  VRF/site separation), persistence (retire/restore, failed reads, route cap), the
  real SNMP lab agent serving IP-MIB/IP-FORWARD-MIB, and the API (tenant
  isolation with identical subnets, foreign site 404, auth, query budget).
* `frontend/tests/topology/l3.test.ts`.

## Known limits

* SNMP walks use GETNEXT (one round trip per row); GETBULK would cut poll time on large route tables.
* The FortiGate `ip` field shape is validated against the documented API and fixtures, not yet a live unit.
* L3 links carry no reachability state; they are drawn as present.
* The page has no L3 detail drawer: selecting a pipe or diamond only highlights it.

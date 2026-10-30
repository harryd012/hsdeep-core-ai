# Device Support & Monitoring Protocols (Sections 15 & 16)

## Device support — categories

Inventory, monitoring, health, topology and automation are supported across:

- **Network devices:** Cisco (IOS, IOS-XE, NX-OS, IOS-XR, ASA, Firepower,
  SD-WAN, wireless controllers/APs, Meraki), Juniper, Arista, HPE, Aruba,
  Fortinet, Palo Alto, Check Point, Sophos, SonicWall, F5, Extreme, Dell,
  Huawei, MikroTik, Ubiquiti, and all SNMP-compatible vendors.
- **Security devices:** NGFW, IPS/IDS, secure web gateways, VPN concentrators,
  WAFs, endpoint/email security, NAC, SIEM-adjacent infrastructure.
- **Compute & OS:** Windows Server/endpoints, Linux/Unix, VMware ESXi/vCenter,
  Hyper-V, Kubernetes nodes, containers, physical servers, VMs, storage,
  backup appliances.
- **Cloud:** AWS / Azure / GCP, private cloud, OpenStack, VMware private cloud,
  cloud networking/firewalls/load balancers/databases/storage/monitoring.
- **Facilities & other:** WAPs/controllers, printers, UPS/power, environmental
  sensors, DC equipment, IoT, industrial devices.

Vocabulary lives in `backend/app/models/device_types.py`:
- `CANONICAL_DEVICE_TYPES` (incl. security_appliance, wireless_controller,
  load_balancer, wan_optimizer, hypervisor, backup_appliance, storage,
  cloud_resource).
- `VENDOR_CATALOG` / `ALL_VENDORS` / `vendor_families()`.
- `DEVICE_FAMILY_TO_TYPE` maps Section 15 families to canonical types.

## Collector / connector architecture

Registered via the `CollectorRegistry` (`BaseCollector` adapter pattern) in
`backend/app/collectors/`:

| source_type   | protocol(s)                              | implemented |
|---------------|------------------------------------------|-------------|
| `snmp`        | SNMP v1/v2c/v3                           | yes         |
| `prtg`        | PRTG HTTP API (sensor/device/message)    | yes         |
| `zabbix`      | Zabbix API                               | yes         |
| `meraki`      | Meraki Dashboard API (MR/MS/MX/MV/MT/CW) | yes         |
| `fortigate`   | FortiGate REST API                       | yes         |
| `fortimanager`| FortiManager API                         | yes         |
| `azure`       | Azure Management API                     | yes         |
| `vmware`      | vSphere / vCenter SDK                    | yes         |
| `linux_ssh`   | SSH (agentless)                          | yes         |
| `windows_winrm`| WinRM / WMI                             | yes         |
| `connectivity`| ICMP / TCP / DNS (agentless)             | yes (added) |

The **added** `connectivity` collector (`backend/app/collectors/connectivity.py`,
stdlib only) performs ICMP echo, TCP port probes and DNS resolution checks with
best-effort latency/packet-loss sensing — ideal for air-gapped/offline and
vendor-agnostic reachability monitoring.

## Protocol coverage

The architecture represents (via connectors or gateway stubs): SNMP v2c/v3,
ICMP, TCP/UDP checks, DNS, HTTP(S), SSH, Telnet (only when explicitly enabled
and secured), NETCONF, RESTCONF, REST/vendor APIs, WMI, WinRM, Windows/Linux
agents, agentless monitoring, Syslog, NetFlow/sFlow/IPFIX, streaming telemetry,
gNMI (where supported), LLDP/CDP, ARP/MAC/routing tables, BGP/OSPF/EIGRP/IS-IS,
MPLS/LDP, VPN/tunnel status, cloud provider APIs, Kubernetes APIs, VMware APIs.

## Credential safety

Credentials are **never stored in plaintext**: they use secure references,
Fernet encryption at rest (`api_key_encrypted` / `password_encrypted`),
rotation, least-privilege access, and audit logging. See
`docs/engineering/security.md`.

## Documented limitations

- SNMP collector covers a subset of standard MIBs (system + interface table).
- The `connectivity` ping parser is best-effort per platform and returns
  `None` rather than fabricating latency when the output is unparsable.
- Additional connectors (BGP/OSPF/NetFlow streaming, gNMI, Kubernetes API) are
  represented at the architecture/registry level; only the listed collectors
  above are implemented end-to-end.

## Tests

`tests/test_snmp_collector.py`, `test_prtg_collector.py`, `test_zabbix_collector.py`,
`test_meraki_collector.py`, `test_fortigate_collector.py`, `test_fortimanager_collector.py`,
`test_azure_collector.py`, `test_vmware_collector.py`, `test_linux_ssh_collector.py`,
`test_windows_winrm_collector.py`, and the added `test_connectivity_collector.py`.
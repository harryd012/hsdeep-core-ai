"""
Seed the SOP Repository with production runbooks SOP-002 through SOP-022
(SOP-002 to SOP-014 were previously hardcoded in the frontend; SOP-015 is MPLS
L2VPN/L3VPN troubleshooting; SOP-016 is the general internet-down triage; SOP-017 to SOP-022 are the SOC
playbooks in scripts/sop_soc_library.py). SOP-001 already exists.
Idempotent: keyed on (tenant_id, sop_number).
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from sqlalchemy.ext.asyncio import AsyncSession
from app.core.db import AsyncSessionLocal
from app.repositories.sop_repository import SOPRepository
from scripts.sop_soc_library import SOC_SEEDS  # SOP-017..021, SOC playbooks

SEEDS = [
    {
        "sop_number": "SOP-002", "name": "Disk Space", "category": "capacity",
        "description": "Investigate and remediate low disk space alerts across Windows/Linux servers.",
        "tags": ["disk", "storage", "capacity", "alert"], "device_type": "server",
        "required_skills": ["Windows Admin", "Linux Admin"], "requires_approval": False,
        "estimated_resolution_minutes": 60, "rollback_procedure": "Snapshot VM before deletions.",
        "steps": [
            {"order": 1, "action": "Identify the affected server and mounted volume from the alert."},
            {"order": 2, "action": "Check current usage via PRTG disk sensor / `df -h` or `Get-PSDrive`."},
            {"order": 3, "action": "Identify large files and clear temp/logs older than 30 days."},
            {"order": 4, "action": "Run filesystem cleanup (Windows Disk Cleanup / `journalctl --vacuum-time=14d`)."},
            {"order": 5, "action": "Verify free space recovers above threshold and alert clears."},
            {"order": 6, "action": "If repeated, schedule a storage expansion or log-rotation policy change."},
        ],
        "possible_causes": ["Runaway log files", "Temp file accumulation", "Application cache growth", "Backups filling disk"],
    },
    {
        "sop_number": "SOP-003", "name": "High CPU", "category": "performance",
        "description": "Diagnose and resolve sustained high CPU utilization on monitored servers.",
        "tags": ["cpu", "performance", "alert"], "device_type": "server",
        "required_skills": ["Windows Admin", "Linux Admin"], "requires_approval": False,
        "estimated_resolution_minutes": 45,
        "rollback_procedure": "Restart the affected service if a faulty app was restarted during triage.",
        "steps": [
            {"order": 1, "action": "Confirm the affected host and CPU threshold from the PRTG sensor."},
            {"order": 2, "action": "Identify top consumers: `top`/`htop` on Linux, Task Manager on Windows."},
            {"order": 3, "action": "Correlate the spike with recent deployments, scheduled jobs, or backups."},
            {"order": 4, "action": "If an app is misbehaving, restart the service or recycle the app pool."},
            {"order": 5, "action": "If a runaway script is found, kill it and notify the owning team."},
            {"order": 6, "action": "Verify CPU normalizes; if it stays high, escalate to the platform team."},
        ],
        "possible_causes": ["Runaway process", "Backup/AV scan window", "Application memory leak", "Cryptominer", "Misconfigured scheduled task"],
    },
    {
        "sop_number": "SOP-004", "name": "Meraki AP Alert", "category": "connectivity",
        "description": "Respond to Meraki wireless access point alerts - offline APs, radio interference, or client disconnects.",
        "tags": ["meraki", "wifi", "ap", "wireless", "alert"], "vendor": "Cisco Meraki", "platform": "meraki",
        "device_type": "access_point", "required_skills": ["Network Admin", "Meraki Dashboard"],
        "requires_approval": False, "estimated_resolution_minutes": 30,
        "rollback_procedure": "Restore previous firmware/config if a firmware upgrade caused the problem.",
        "steps": [
            {"order": 1, "action": "Open the Meraki Dashboard and locate the alerting AP under Wireless > Access Points."},
            {"order": 2, "action": "Check AP status: online/offline, firmware version, and RF channel."},
            {"order": 3, "action": "Verify PoE port on the switch: power, link, and cable seating."},
            {"order": 4, "action": "Check recent events for the AP in Meraki for reboot or deauth loops."},
            {"order": 5, "action": "If interference is flagged, change the channel/width via RF profile."},
            {"order": 6, "action": "If still offline, reboot the AP remotely; if no response, send an onsite tech."},
        ],
        "possible_causes": ["PoE port failure", "Cable fault", "AP firmware bug", "RF interference", "Switch port disabled"],
    },
    {
        "sop_number": "SOP-005", "name": "DNS Sensor Verification & Configuration in PRTG", "category": "configuration",
        "description": "Verify or (re)configure DNS sensors in PRTG when DNS resolution alerts fire or sensors stop reporting.",
        "tags": ["prtg", "dns", "sensor", "configuration"], "vendor": "Paessler", "platform": "prtg",
        "device_type": "sensor", "required_skills": ["NOC Operator", "PRTG Admin"], "requires_approval": False,
        "estimated_resolution_minutes": 20, "rollback_procedure": "Restore the previous sensor configuration.",
        "steps": [
            {"order": 1, "action": "Open PRTG and locate the DNS sensor under the target device."},
            {"order": 2, "action": "Verify the sensor is enabled and the target DNS server/IP is correct."},
            {"order": 3, "action": "Test resolution manually: `nslookup <domain> <server>` from the probe."},
            {"order": 4, "action": "If the sensor is misconfigured, update the lookup domain/nameserver settings and rescan."},
            {"order": 5, "action": "Pause/simulate a check; confirm the sensor returns OK and goes green."},
            {"order": 6, "action": "Document the change in the runbook tracker/change log."},
        ],
        "possible_causes": ["Wrong nameserver configured", "Sensor disabled after maintenance", "DNS server unreachable from probe", "Typo in lookup domain"],
    },
    {
        "sop_number": "SOP-006", "name": "Site Internet Down – FortiGate Ping Down / Starlink Static IP Change", "category": "connectivity",
        "description": "Diagnose site-wide internet outages where the FortiGate WAN link is ping-down, including Starlink static IP changes.",
        "tags": ["fortigate", "internet", "starlink", "wan", "outage"], "vendor": "Fortinet", "platform": "fortigate",
        "device_type": "firewall", "required_skills": ["Network Admin", "FortiGate"], "requires_approval": True,
        "estimated_resolution_minutes": 90, "rollback_procedure": "Revert routing/static IP changes if connectivity degrades.",
        "steps": [
            {"order": 1, "action": "Confirm the site is fully offline: ping WAN IP, check FortiGate dashboard for link status."},
            {"order": 2, "action": "Check ISP status portal for regional outages."},
            {"order": 3, "action": "Power-cycle the ISP modem/router (Starlink/SDH/leased line) and wait for sync."},
            {"order": 4, "action": "If Starlink static IP changed, update the FortiGate's static IP / DDNS / IPsec peer config."},
            {"order": 5, "action": "Verify BGP/static route is up and site traffic flows; run a speed test."},
            {"order": 6, "action": "If still down, escalate to the ISP with the ticket reference and schedule an onsite visit."},
        ],
        "possible_causes": ["ISP outage", "Starlink static IP changed", "FortiGate WAN interface down", "Modem/router unresponsive", "Fiber/cable cut"],
    },
    {
        "sop_number": "SOP-007", "name": "Restart IIS Application Pools Using Kaseya VSA (PRTG Alert Handling)", "category": "availability",
        "description": "Remotely recycle a down/impacted IIS application pool via Kaseya VSA when a PRTG web/app sensor alerts.",
        "tags": ["iis", "kaseya", "prtg", "app-pool", "web"], "vendor": "Microsoft", "platform": "kaseya",
        "device_type": "server", "required_skills": ["Windows Admin", "Kaseya VSA", "IIS"], "requires_approval": True,
        "estimated_resolution_minutes": 15, "rollback_procedure": "If recycling does not restore the site, restart IIS entirely (`iisreset`).",
        "steps": [
            {"order": 1, "action": "From the PRTG alert, identify the affected server and IIS site/app pool."},
            {"order": 2, "action": "Open Kaseya VSA and locate the endpoint; take a live/remote session."},
            {"order": 3, "action": "Run `Get-ChildItem IIS:\\AppPools` to confirm the pool name and state."},
            {"order": 4, "action": "Recycle the pool: `Restart-WebAppPool -Name \"<pool>\"`."},
            {"order": 5, "action": "Verify the site serves HTTP 200 locally and the PRTG sensor goes green."},
            {"order": 6, "action": "Log the action and any root-cause notes in the ticket."},
        ],
        "possible_causes": ["Application hang", "Memory leak in worker process", "Locked files", "Bad web.config deployment"],
    },
    {
        "sop_number": "SOP-008", "name": "Inactive Property DNS Sensor Alert (MX/SPF/DMARC Down)", "category": "security",
        "description": "Investigate DNS sensor alerts for inactive properties - MX, SPF, and DMARC records failing to resolve.",
        "tags": ["dns", "mx", "spf", "dmarc", "email", "security"], "platform": "prtg", "device_type": "sensor",
        "required_skills": ["NOC Operator", "DNS Admin"], "requires_approval": False, "estimated_resolution_minutes": 45,
        "rollback_procedure": "Restore the previous DNS record values if a change broke mail flow.",
        "steps": [
            {"order": 1, "action": "Identify the property/domain from the PRTG DNS sensor alert."},
            {"order": 2, "action": "Check MX records: `nslookup -type=mx <domain>` or dig."},
            {"order": 3, "action": "Check SPF (TXT `v=spf1`) and DMARC (`_dmarc.<domain>` TXT) records."},
            {"order": 4, "action": "Verify authoritative nameservers and that the zone is active at the DNS provider."},
            {"order": 5, "action": "If records are missing, restore from the last known-good values / DNS backup."},
            {"order": 6, "action": "Confirm propagation with an external resolver and clear the alert."},
        ],
        "possible_causes": ["Domain/SSL expired", "DNS zone deleted", "Registrar nameserver change", "Missing MX/SPF/DMARC records"],
    },
    {
        "sop_number": "SOP-009", "name": "Internet Outage – ISP Router Missing Static IP", "category": "connectivity",
        "description": "Handle internet outages caused by an ISP router losing its static IP configuration.",
        "tags": ["isp", "router", "static-ip", "internet", "outage"], "device_type": "router",
        "required_skills": ["Network Admin"], "requires_approval": True, "estimated_resolution_minutes": 120,
        "rollback_procedure": "Re-apply the documented static IP if the ISP router resets to DHCP.",
        "steps": [
            {"order": 1, "action": "Confirm site internet is down (all WAN sensors red)."},
            {"order": 2, "action": "Log into the ISP router and check WAN IP vs. the documented static IP."},
            {"order": 3, "action": "If the router is on DHCP (static IP lost), re-enter the static IP/gateway/DNS."},
            {"order": 4, "action": "Verify the public IP matches what the ISP assigned and test connectivity."},
            {"order": 5, "action": "Contact the ISP if the IP won't stick; request a config restore."},
            {"order": 6, "action": "Monitor the link for 30 minutes before closing the incident."},
        ],
        "possible_causes": ["ISP router factory reset", "ISP DHCP reassignment", "Router firmware update wiped config", "Static IP binding lost at ISP"],
    },
    {
        "sop_number": "SOP-010", "name": "Meraki Site Offline – MX / MS / MR Devices Down", "category": "connectivity",
        "description": "Triage a full Meraki site outage where MX, MS, and MR devices all drop offline.",
        "tags": ["meraki", "mx", "ms", "mr", "site-down"], "vendor": "Cisco Meraki", "platform": "meraki",
        "device_type": "network", "required_skills": ["Network Admin", "Meraki Dashboard"], "requires_approval": True,
        "estimated_resolution_minutes": 90, "rollback_procedure": "Restore ISP link/power; revert any config change made during triage.",
        "steps": [
            {"order": 1, "action": "Open Meraki Dashboard > Organization > Overview; confirm all site devices are offline."},
            {"order": 2, "action": "Check uplink status on the MX: WAN IPSec/Internet link, and whether MTU/power caused the drop."},
            {"order": 3, "action": "Verify physical power at the site / UPS state (coordinate with onsite staff or smart PDU)."},
            {"order": 4, "action": "Check ISP circuit status and any scheduled maintenances."},
            {"order": 5, "action": "If the MX is up but MS/MR are not, power-cycle the PoE switch stack."},
            {"order": 6, "action": "As devices come back, verify SSIDs, VLANs, and site-to-site VPN tunnels."},
        ],
        "possible_causes": ["Site power loss", "ISP circuit down", "MX failure", "Switch stack crash", "Maintenance window outage"],
    },
    {
        "sop_number": "SOP-011", "name": "Adding Devices & Sensors in PRTG (Device Onboarding)", "category": "configuration",
        "description": "Standard procedure for onboarding new devices and sensors into PRTG monitoring.",
        "tags": ["prtg", "onboarding", "device", "sensor", "configuration"], "vendor": "Paessler", "platform": "prtg",
        "device_type": "device", "required_skills": ["PRTG Admin"], "requires_approval": True, "estimated_resolution_minutes": 45,
        "rollback_procedure": "Pause/delete the device and its sensors if monitoring degrades the monitored host.",
        "steps": [
            {"order": 1, "action": "Gather device details: hostname, IP, credentials, vendor, SNMP/WMI/API access."},
            {"order": 2, "action": "Add the device in PRTG under the correct group/site folder."},
            {"order": 3, "action": "Enter credentials and select the auto-discovery sensors (SNMP/WMI/NetFlow)."},
            {"order": 4, "action": "Tune thresholds - warning/critical limits for CPU, memory, disk, and ping."},
            {"order": 5, "action": "Verify sensors return OK values; correct any red sensors."},
            {"order": 6, "action": "Add to relevant maps/notifications and document in the asset/CMDB."},
        ],
        "possible_causes": ["N/A - new device onboarding"],
    },
    {
        "sop_number": "SOP-012", "name": "Qualys Patch Deployment Job Missing – Critical Runtime Servers", "category": "security",
        "description": "Investigate missing Qualys patch deployment jobs on critical runtime servers and re-run the deployment.",
        "tags": ["qualys", "patch", "deployment", "security", "server"], "vendor": "Qualys", "platform": "qualys",
        "device_type": "server", "required_skills": ["Patch Admin", "Qualys"], "requires_approval": True,
        "estimated_resolution_minutes": 60, "rollback_procedure": "Use Qualys rollback/remediation history to uninstall a bad patch.",
        "steps": [
            {"order": 1, "action": "Identify the critical server and the expected Qualys patch job from the alert/ticket."},
            {"order": 2, "action": "Open Qualys Patch Management and check job status for the affected asset."},
            {"order": 3, "action": "Confirm the server agent is online and reporting to the correct Qualys platform."},
            {"order": 4, "action": "If the job never ran, create/queue a deployment job for the missing patch set."},
            {"order": 5, "action": "Monitor the deployment; verify installed patches via Qualys/`Get-HotFix`."},
            {"order": 6, "action": "Reboot if required, then confirm the server returns healthy and the job completes."},
        ],
        "possible_causes": ["Agent offline", "Job scheduling failure", "Maintenance window collision", "Credential/auth failure on target", "Patch superseded"],
    },
    {
        "sop_number": "SOP-013", "name": "Server / Device Commissioning and Decommissioning Process", "category": "configuration",
        "description": "Controlled process to commission new or decommission retired servers/devices across monitoring, CMDB, and backups.",
        "tags": ["commissioning", "decommissioning", "lifecycle", "server", "device"], "device_type": "server",
        "required_skills": ["Infra Admin", "CMDB Admin"], "requires_approval": True, "estimated_resolution_minutes": 120,
        "rollback_procedure": "For decommission, retain a full backup/snapshot for 30 days before permanent removal.",
        "steps": [
            {"order": 1, "action": "Raise a change request / ticket for the commissioning or decommissioning task."},
            {"order": 2, "action": "Commission: provision in PRTG, CMDB/asset inventory, DNS, and monitoring groups."},
            {"order": 3, "action": "Commission: add to patching (Qualys), backup, and alert routing policies."},
            {"order": 4, "action": "Decommission: notify stakeholders, back up config/data, and stop monitoring first."},
            {"order": 5, "action": "Decommission: remove from PRTG, CMDB, DNS, backups, and monitoring after grace period."},
            {"order": 6, "action": "Document the change in the asset lifecycle log and close the ticket."},
        ],
        "possible_causes": ["N/A - lifecycle process"],
    },
    {
        "sop_number": "SOP-014", "name": "UPS Alert Diagnosis – Perform UPS Self-Test", "category": "availability",
        "description": "Diagnose UPS alerts and perform a battery self-test to verify runtime and health.",
        "tags": ["ups", "power", "battery", "self-test"], "device_type": "ups",
        "required_skills": ["NOC Operator", "Facilities Contact"], "requires_approval": True, "estimated_resolution_minutes": 30,
        "rollback_procedure": "Return UPS to normal/online mode if the self-test triggers an alarm condition.",
        "steps": [
            {"order": 1, "action": "Identify the UPS from the alert and confirm its current mode (online/battery/bypass)."},
            {"order": 2, "action": "Check load %, battery %, and battery health via SNMP/management card."},
            {"order": 3, "action": "Review recent events: input voltage sags, battery test warnings, or bypass events."},
            {"order": 4, "action": "Initiate a self-test per vendor: APC `test` command, Eaton/MGE card, or front-panel button."},
            {"order": 5, "action": "Monitor the test - verify battery holds runtime and the test completes successfully."},
            {"order": 6, "action": "If battery fails, schedule a battery/set replacement and escalate to facilities."},
        ],
        "possible_causes": ["Battery aging", "Input power instability", "Overload condition", "Charger fault", "Test failure from previous deep discharge"],
    },
    {
        "sop_number": "SOP-015",
        "name": "MPLS L2VPN/L3VPN Troubleshooting",
        "description": "Troubleshooting framework for MPLS L2VPN Ethernet frame transport and L3VPN customer prefix routing through provider VRFs",
        "category": "connectivity",
        "tags": [
            "mpls",
            "l2vpn",
            "l3vpn",
            "vrf",
            "bgp",
            "ethernet",
            "wan",
            "carrier-noc"
        ],
        "vendor": "Provider",
        "platform": "MPLS Core",
        "steps": [
            {
                "order": 1,
                "action": "Check physical link status and interface statistics on PE-CE boundary",
                "expected_result": "Interface is up with no error packets",
                "warnings": [
                    "Verify physical cable connectivity first"
                ],
                "troubleshooting_matrix": {
                    "no_link": "Check cable, transceivers, physical layer",
                    "errors": "Check interface errors, logging, MTU mismatch"
                },
                "commands": [
                    {
                        "platform": "cisco_nxos",
                        "version": "any",
                        "command": "show interface",
                        "authorized_provider_only": True
                    },
                    {
                        "platform": "juniper_junos",
                        "version": "any",
                        "command": "show interfaces extensive",
                        "authorized_provider_only": True
                    }
                ]
            },
            {
                "order": 2,
                "action": "Verify VLAN tagging and EVC/pseudowire status",
                "expected_result": "EVC status up, pseudowire up",
                "warnings": [
                    "mismatched VLAN tags will cause frame loss"
                ],
                "troubleshooting_matrix": {
                    "pseudowire_down": "Check PW status, labeling, routing instance",
                    "vlan_mismatch": "Verify CE-VLAN matching EVC configuration"
                },
                "commands": [
                    {
                        "platform": "cisco_nxos",
                        "version": "any",
                        "command": "show mpls l2vpn evc",
                        "authorized_provider_only": True
                    }
                ]
            },
            {
                "order": 3,
                "action": "Confirm MAC learning on L2VPN",
                "expected_result": "MAC addresses learned and visible in FIB",
                "warnings": [
                    "aged MAC entries may stale after link flaps"
                ],
                "troubleshooting_matrix": {
                    "missing_mac": "Check EVC, pseudowire, CE device config",
                    "mac_flapping": "Verify STP/RSTP status on Ethernet segments"
                },
                "commands": [
                    {
                        "platform": "cisco_nxos",
                        "version": "any",
                        "command": "show mpls forwarding-table",
                        "authorized_provider_only": True
                    }
                ]
            },
            {
                "order": 4,
                "action": "Check MTU consistency across the EVC",
                "expected_result": "MTU matches across CE-PE link and within EVC",
                "warnings": [
                    "MTU mismatch causes packet drop or fragmentation"
                ],
                "troubleshooting_matrix": {
                    "mtu_mismatch": "Align MTU across all EVC endpoints",
                    "exceed_mtu": "Check for jumbo frame configuration"
                },
                "commands": [
                    {
                        "platform": "cisco_nxos",
                        "version": "any",
                        "command": "show interface mtu",
                        "authorized_provider_only": True
                    }
                ]
            },
            {
                "order": 5,
                "action": "Verify STP/RSTP status where applicable",
                "expected_result": "No blocking states, consistent timers",
                "warnings": [
                    "STP convergence delays can impact failover"
                ],
                "troubleshooting_matrix": {
                    "blocking_state": "Check STP topology, root bridge placement",
                    "timers_mismatch": "Verify STP timers across switches"
                },
                "commands": [
                    {
                        "platform": "cisco_nxos",
                        "version": "any",
                        "command": "show spanning-tree",
                        "authorized_provider_only": True
                    }
                ]
            },
            {
                "order": 6,
                "action": "Check CE-PE adjacency using BGP or static routes",
                "expected_result": "CE-PE session established, routes received",
                "warnings": [
                    "Adjacency failures indicate misconfigured VRF or ACLs"
                ],
                "troubleshooting_matrix": {
                    "bgp_peering_fail": "Verify BGP config, route-targets, VRF instance",
                    "static_route_fail": "Check static route reachability, VRF-lite"
                },
                "commands": [
                    {
                        "platform": "cisco_ios",
                        "version": "any",
                        "command": "show ip bgp vrf CUSTOMER1",
                        "authorized_provider_only": True
                    }
                ]
            },
            {
                "order": 7,
                "action": "Verify advertised and received routes in PE VRF",
                "expected_result": "Customer prefixes present in BGP table, correct next-hop",
                "warnings": [
                    "Stale routes indicate peer instability"
                ],
                "troubleshooting_matrix": {
                    "missing_routes": "Check route-target import/export, RT mismatch",
                    "wrong_next_hop": "Validate PE next-hop address, label allocation"
                },
                "commands": [
                    {
                        "platform": "cisco_nxos",
                        "version": "any",
                        "command": "show ip bgp vpnv4 all",
                        "authorized_provider_only": True
                    }
                ]
            },
            {
                "order": 8,
                "action": "Validate return path and provider MPLS core health",
                "expected_result": "Return path operational, MPLS labels propagated core-to-core",
                "warnings": [
                    "Label imposition/imposition failures indicate core issues"
                ],
                "troubleshooting_matrix": {
                    "label_missing": "Check LSDP, LDP session, core connectivity",
                    "core_congestion": "Verify no core congestion, label TTL handling"
                },
                "commands": [
                    {
                        "platform": "cisco_nxos",
                        "version": "any",
                        "command": "show mpls lsp",
                        "authorized_provider_only": True
                    }
                ]
            }
        ],
        "possible_causes": [
            "Physical link failure",
            "VLAN misconfiguration",
            "PW signaling failure",
            "MAC learning failure",
            "MTU mismatch",
            "STP blocking",
            "CE-PE BGP peering failure",
            "Route-target mismatch",
            "MPLS label distribution failure"
        ]
    },
    {
        "sop_number": "SOP-016",
        "name": "Internet Down - Devices Connected but No Internet (Layered Triage)",
        "description": (
            "General first-response triage when users report that devices are connected to the LAN or Wi-Fi "
            "but nothing on the internet works. Works up the path one layer at a time - device, link, "
            "LAN/switch, router/firewall WAN, ISP access (fibre ONU/ONT/OLT, DSL, cellular) - so the root "
            "cause is found rather than guessed. Read-only diagnostics; any reboot or config change needs "
            "a change record. Hands over to SOP-006 (FortiGate / Starlink IP change) or SOP-009 (ISP "
            "router missing static IP) when the evidence points there."
        ),
        "category": "connectivity",
        "tags": ["internet", "outage", "wan", "dns", "gateway", "isp", "ping", "fiber", "gpon", "onu", "ont", "triage"],
        "required_skills": ["Network Admin"],
        "requires_approval": False,
        "estimated_resolution_minutes": 30,
        "rollback_procedure": (
            "Diagnostic steps change nothing. If a router, firewall or ONU/ONT restart is needed, raise a "
            "change record first and note the pre-change state (WAN IP, routes, optical levels)."
        ),
        "steps": [
            {
                "order": 1,
                "action": "Define the scope: one user, one site, or every site. Check the HSDEEP dashboard for the site's devices, WAN/ping sensors and any open incident before touching anything.",
                "expected_result": "You know which devices and sites are affected and when it started.",
                "warnings": ["Do not restart equipment before you know the scope - a restart destroys the evidence."],
                "troubleshooting_matrix": {
                    "one_user_only": "Device problem: go to step 4 on that device (IP, gateway, DNS, proxy, VPN client).",
                    "one_site": "Site access problem: continue with step 2 at that site.",
                    "all_sites": "Shared dependency (data centre, ISP core, cloud DNS, firewall policy push): escalate to the NOC lead and continue with step 5 from two sites.",
                },
            },
            {
                "order": 2,
                "action": "Check physical connection and link status: cables, connectors and port LEDs on the router/firewall WAN port, the LAN uplink and the ONU/ONT or modem.",
                "expected_result": "Link lights on (green/amber steady), WAN port reports link up at the expected speed and duplex.",
                "warnings": ["A link that is up at 10/100 instead of 1000 can point to a damaged cable."],
                "troubleshooting_matrix": {
                    "no_link_light": "Reseat or replace the cable or SFP; check power to the ONU/ONT or modem.",
                    "link_flapping": "Check interface error counters (step 3); suspect cable, SFP or duplex mismatch.",
                },
                "commands": [
                    {"platform": "cisco_ios", "version": "any", "command": "show interfaces status"},
                    {"platform": "fortios", "version": "any", "command": "get system interface physical"},
                    {"platform": "meraki", "version": "any", "command": "Dashboard > Security & SD-WAN > Appliance status > Uplink"},
                ],
            },
            {
                "order": 3,
                "action": "Verify router and switch interfaces: WAN and LAN interface state, error and discard counters, and congestion on the uplink.",
                "expected_result": "Interfaces up/up, error counters not increasing, uplink utilisation below saturation.",
                "warnings": ["Clear counters only after recording them, so the change is visible on the next read."],
                "troubleshooting_matrix": {
                    "interface_down_down": "Physical or upstream problem: back to step 2, or step 7 for the ISP access device.",
                    "up_down": "Protocol problem: check PPPoE/DHCP on the WAN, VLAN tagging required by the ISP.",
                    "errors_increasing": "Cable, SFP or duplex mismatch.",
                    "saturated_uplink": "Not an outage but congestion: find the top talkers before escalating.",
                },
                "commands": [
                    {"platform": "cisco_ios", "version": "any", "command": "show interfaces <wan_interface>"},
                    {"platform": "cisco_ios", "version": "any", "command": "show ip interface brief"},
                    {"platform": "fortios", "version": "any", "command": "diagnose hardware deviceinfo nic <wan_port>"},
                    {"platform": "linux", "version": "any", "command": "ip -s link show"},
                ],
            },
            {
                "order": 4,
                "action": "Check IP address and default gateway: the client has a valid address (not 169.254.x.x), the right subnet, a default gateway, and the router's WAN has a public or ISP-assigned address and a default route.",
                "expected_result": "Client addressing is valid; the router WAN has an address and a default route via the ISP.",
                "warnings": ["A 169.254.x.x address means DHCP failed - the internet may be fine."],
                "troubleshooting_matrix": {
                    "apipa_169_254": "DHCP server/relay or scope exhausted: check the DHCP pool on the router or server.",
                    "no_wan_ip": "PPPoE/DHCP with the ISP failed: check credentials and the ISP; if a static IP is expected see SOP-009.",
                    "wan_ip_changed": "Static or dynamic IP changed (for example Starlink): see SOP-006.",
                    "no_default_route": "Routing problem on the edge device: check the static route or DHCP-learnt route.",
                },
                "commands": [
                    {"platform": "windows", "version": "any", "command": "ipconfig /all"},
                    {"platform": "linux", "version": "any", "command": "ip addr; ip route"},
                    {"platform": "cisco_ios", "version": "any", "command": "show ip route 0.0.0.0"},
                    {"platform": "fortios", "version": "any", "command": "get router info routing-table all"},
                ],
            },
            {
                "order": 5,
                "action": "Test connectivity outward with ping, one hop at a time: the default gateway, then a public IP address (for example 8.8.8.8 or 1.1.1.1), then a traceroute to see where it stops. Note packet loss and latency.",
                "expected_result": "Gateway and public IP answer with no loss.",
                "warnings": ["Some networks block ICMP: if ping fails, also test TCP (for example `Test-NetConnection 1.1.1.1 -Port 443`)."],
                "troubleshooting_matrix": {
                    "gateway_fails": "LAN problem between client and router: steps 2-4 on the LAN side.",
                    "gateway_ok_public_ip_fails": "Problem at the edge or beyond: router WAN, firewall policy/NAT, or the ISP (step 7).",
                    "public_ip_ok": "IP connectivity works: the problem is name resolution, a proxy or the application - go to step 6.",
                    "intermittent_loss": "Congestion or a failing link: collect loss over time (step 8 evidence).",
                },
                "commands": [
                    {"platform": "windows", "version": "any", "command": "ping <gateway>; ping -n 20 8.8.8.8; tracert -d 8.8.8.8"},
                    {"platform": "linux", "version": "any", "command": "ping -c 20 <gateway>; ping -c 20 8.8.8.8; traceroute -n 8.8.8.8"},
                    {"platform": "cisco_ios", "version": "any", "command": "ping 8.8.8.8 source <wan_interface> repeat 20"},
                    {"platform": "fortios", "version": "any", "command": "execute ping 8.8.8.8"},
                ],
            },
            {
                "order": 6,
                "action": "Check DNS resolution: resolve a well-known name with the configured DNS server and with a public resolver. If ping to a public IP works but websites do not open, DNS is the first suspect.",
                "expected_result": "Names resolve with the configured server.",
                "warnings": ["Do not leave clients permanently on a public DNS server if the site needs internal DNS (Active Directory)."],
                "troubleshooting_matrix": {
                    "fails_on_configured_works_on_public": "Internal/forwarding DNS problem: check the DNS server, its forwarders and firewall rules for port 53.",
                    "fails_everywhere": "Outbound DNS blocked or no IP connectivity: back to step 5.",
                    "resolves_but_sites_fail": "Proxy, TLS inspection, captive portal or application issue.",
                },
                "commands": [
                    {"platform": "windows", "version": "any", "command": "nslookup example.com; nslookup example.com 8.8.8.8"},
                    {"platform": "linux", "version": "any", "command": "dig example.com; dig @8.8.8.8 example.com"},
                    {"platform": "fortios", "version": "any", "command": "execute ping example.com"},
                ],
            },
            {
                "order": 7,
                "action": "If the site uses an ISP fibre (GPON) or other access device, check the access layer: ONU/ONT power, PON/LOS lights, registration on the OLT (ISP side), and optical receive (Rx) and transmit (Tx) levels.",
                "expected_result": "PON light steady, LOS off, the ONU registered, optical Rx within the ISP's range (typically about -8 to -27 dBm for GPON).",
                "warnings": [
                    "OLT checks are done by the ISP or by staff with ISP access only.",
                    "Never look into a fibre connector or a disconnected port.",
                ],
                "troubleshooting_matrix": {
                    "los_red_or_pon_off": "Fibre cut, dirty or loose connector, or ONU not provisioned: ISP fault - escalate (step 8).",
                    "rx_too_low": "Dirty connector, bent or damaged fibre: ISP or field visit.",
                    "onu_ok_no_ip": "Provisioning or authentication at the ISP: escalate with the ONU serial number.",
                },
                "commands": [
                    {"platform": "onu_web", "version": "any", "command": "ONU/ONT web UI > Status > PON/Optical information"},
                    {"platform": "olt", "version": "any", "command": "Vendor OLT: display ONU state and optical info for the subscriber port", "authorized_provider_only": True},
                ],
            },
            {
                "order": 8,
                "action": "Escalate to the ISP when the fault is beyond the customer edge. Open the ticket with evidence, not a description: affected sites, start time, WAN IP and gateway, ping/traceroute output, optical levels, ONU serial number, and what has been ruled out.",
                "expected_result": "An ISP ticket number recorded on the incident, with the evidence attached.",
                "warnings": ["Record the ISP ticket number and promised time on the incident so the SLA clock is visible."],
                "troubleshooting_matrix": {
                    "isp_confirms_outage": "Track the ISP ETA; consider failover to a backup WAN or cellular link if the site has one.",
                    "isp_reports_no_fault": "Re-run steps 2-6 with the ISP on the line; ask for a line test from their side.",
                },
            },
            {
                "order": 9,
                "action": "Verify recovery and record the root cause: users can browse, WAN and ping sensors are green in HSDEEP for at least 15 minutes, and the incident notes say WHY it happened, not only that it is fixed.",
                "expected_result": "Service restored and stable; root cause, evidence and the fix are on the incident.",
                "warnings": ["Do not close the incident on the first successful ping - wait for stable sensors."],
                "troubleshooting_matrix": {
                    "recurs_within_24h": "Reopen the incident and treat it as a problem record with the ISP or vendor.",
                },
            },
        ],
        "possible_causes": [
            "Cable, connector or SFP fault",
            "ONU/ONT or modem without power or fibre signal (LOS)",
            "Low optical receive level (dirty connector, damaged fibre)",
            "ISP outage or provisioning/authentication failure",
            "WAN IP address changed or static IP missing",
            "DHCP failure (client on 169.254.x.x)",
            "Missing default route on the edge device",
            "Firewall policy or NAT change",
            "DNS server or forwarder failure",
            "Uplink congestion",
        ],
    },
]

SEEDS += SOC_SEEDS



async def seed_sops(db: AsyncSession, tenant_id: uuid.UUID) -> int:
    repo = SOPRepository(db)
    created = 0
    for seed in SEEDS:
        existing = await repo.get_by_number(tenant_id, seed["sop_number"])
        if existing is not None:
            print(f"[seed_sops] {seed['sop_number']} already exists - skipped.")
            continue
        steps = seed.pop("steps")
        possible_causes = seed.pop("possible_causes")
        sop = await repo.create(tenant_id=tenant_id, **seed)
        version = await repo.create_version(
            tenant_id=tenant_id, sop_id=sop.id, version_number=1,
            steps=steps, possible_causes=possible_causes,
            change_summary="Initial seed from production runbook list.", status="draft",
        )
        approved = await repo.approve_version(version, approved_by=None)
        await repo.set_current_version(sop, approved)
        print(f"[seed_sops] Created {seed['sop_number']} | {seed['name']} (approved v1).")
        created += 1
    return created


async def main() -> int:
    tenant_id = os.environ.get("SEED_TENANT_ID")
    if not tenant_id:
        print("[seed_sops] Aborted: missing SEED_TENANT_ID in backend/.env")
        return 1
    try:
        async with AsyncSessionLocal() as db:
            created = await seed_sops(db, uuid.UUID(tenant_id))
            await db.commit()
            print(f"[seed_sops] Done: {created} new SOP(s) created.")
            return 0
    except Exception as exc:
        print(f"[seed_sops] Failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
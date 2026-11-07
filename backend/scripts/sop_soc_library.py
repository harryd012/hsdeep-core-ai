"""
SOC playbooks for the SOP Repository: SOP-017 to SOP-022 (seeded by
scripts/seed_sops.py, which imports SOC_SEEDS).

SOP-022 (FortiGate scenarios) is adapted from the HSDEEP FortiGate Firewall
Interview Quick Book supplied by the product owner.

Every SOP carries the ``soc`` tag, which the SOC Playbooks page
(/soc-analysis/playbooks) lists. Steps follow the SOP-015 shape: action,
expected_result, warnings, troubleshooting_matrix (what each outcome means)
and commands per platform. Platform keys:

  fortios         Fortinet FortiGate (FortiOS CLI)
  panos           Palo Alto Networks PAN-OS (CLI)
  cisco_asa       Cisco ASA
  cisco_ftd       Cisco Secure Firewall Threat Defense (FTD CLI)
  checkpoint      Check Point Gaia (clish / expert mode)
  junos_srx       Juniper SRX (Junos CLI)
  sonicwall       SonicWall SonicOS (web UI; menu names vary by SonicOS version)
  sophos          Sophos Firewall SFOS (web UI)
  siem / edr      SIEM search and EDR console (product-neutral)

Diagnostics are read-only unless a step says otherwise. Containment, blocking
and policy changes are marked and need approval; the SOPs that contain them
set requires_approval.
"""
from __future__ import annotations


def _c(platform: str, command: str, **extra) -> dict:
    return {"platform": platform, "version": "any", "command": command, **extra}


FIREWALL_DIAGNOSTIC = {
    "sop_number": "SOP-017",
    "name": "Firewall Diagnostic - Standard Steps (Multi-Vendor)",
    "description": (
        "Standard order for diagnosing a firewall problem - traffic blocked, slow or dropped, HA "
        "failover, VPN down, high load or missing protection - on the main firewall platforms: "
        "FortiGate, Palo Alto, Cisco ASA/FTD, Check Point, Juniper SRX, SonicWall and Sophos. Same "
        "steps for every vendor; commands per platform. Read-only unless marked."
    ),
    "category": "connectivity",
    "device_type": "firewall",
    "tags": ["soc", "firewall", "diagnostic", "ha", "vpn", "nat", "policy", "packet-trace", "ips"],
    "required_skills": ["Network Security", "Firewall Admin"],
    "requires_approval": False,
    "estimated_resolution_minutes": 45,
    "rollback_procedure": (
        "Diagnostics change nothing. Stop any debug or packet capture you started (debug flows and "
        "captures cost CPU). A policy, NAT or routing change needs a change record and a saved "
        "configuration backup first."
    ),
    "steps": [
        {
            "order": 1,
            "action": "Define the problem precisely: source IP, destination IP and port, protocol, application, when it started, and whether it affects one flow, one zone/VLAN, one site or everything. Check the HSDEEP Firewalls page for the device's HA, load, tunnels and evidence age.",
            "expected_result": "A 5-tuple (or a named service) and a start time that every later step can test against.",
            "warnings": ["Without a concrete flow, policy and session checks cannot prove anything."],
            "troubleshooting_matrix": {
                "everything_down": "Start with health and HA (steps 2-3).",
                "one_flow_or_app": "Go to policy and packet trace (step 5).",
                "vpn_only": "Go to VPN (step 8).",
            },
        },
        {
            "order": 2,
            "action": "Check device health: uptime (an unexpected reboot?), CPU, memory, session table use and conserve/overload states.",
            "expected_result": "CPU and memory below 80 %, no conserve mode, session count well below the limit, uptime matches the last planned change.",
            "warnings": ["High CPU on the data plane drops traffic before any log says so."],
            "troubleshooting_matrix": {
                "conserve_or_memory_high": "Memory leak, logging load or session flood: find top sessions/sources, check for a DoS (SOP-018) before restarting anything.",
                "recent_reboot": "Check crash logs and the change record; a reboot can also mean an HA failover.",
                "sessions_near_limit": "Session flood or very long timeouts: list top sources (step 6).",
            },
            "commands": [
                _c("fortios", "get system status; get system performance status; diagnose hardware sysinfo conserve"),
                _c("panos", "show system info; show system resources; show running resource-monitor minute last 5"),
                _c("cisco_asa", "show version; show cpu usage; show memory; show conn count"),
                _c("cisco_ftd", "show version; show cpu usage; show memory; show conn count"),
                _c("checkpoint", "cpstat os -f cpu; cpstat os -f memory; fw ctl pstat"),
                _c("junos_srx", "show chassis routing-engine; show security monitoring; show system uptime"),
                _c("sonicwall", "Web UI: Dashboard > System (CPU, memory, connection count)"),
                _c("sophos", "Web UI: Monitor & analyze > Control center (CPU, memory, connections)"),
            ],
        },
        {
            "order": 3,
            "action": "Check high availability: which unit is active, whether the peer is present and configuration is in sync, and the last failover time and reason.",
            "expected_result": "Expected unit active, peer present, configuration in sync, no unplanned failover.",
            "warnings": ["Never force a failover to 'test' during an incident without approval: it drops sessions."],
            "troubleshooting_matrix": {
                "out_of_sync": "Configuration differs between members: compare checksums; a change was made on the passive unit or sync failed.",
                "peer_missing": "Heartbeat/HA link down or peer failed: check HA cabling and the peer's console.",
                "recent_failover": "Find the trigger (link monitor, path monitor, hardware) before failing back.",
            },
            "commands": [
                _c("fortios", "get system ha status; diagnose sys ha checksum cluster"),
                _c("panos", "show high-availability state; show high-availability state-synchronization"),
                _c("cisco_asa", "show failover; show failover history"),
                _c("cisco_ftd", "show failover; show failover history"),
                _c("checkpoint", "cphaprob state; cphaprob -a if"),
                _c("junos_srx", "show chassis cluster status; show chassis cluster interfaces"),
                _c("sonicwall", "Web UI: High Availability > Status"),
                _c("sophos", "Web UI: Configure > System services > High availability"),
            ],
        },
        {
            "order": 4,
            "action": "Check interfaces and routing for the flow: ingress and egress interface up, no errors, and the route to the destination points out of the expected interface (including policy-based routing and SD-WAN rules).",
            "expected_result": "Both interfaces up without increasing errors; the route lookup returns the expected egress and next hop.",
            "warnings": ["An asymmetric path (return traffic through another firewall) looks exactly like a policy drop."],
            "troubleshooting_matrix": {
                "interface_down_or_errors": "Physical/link problem: cabling, SFP, switch port.",
                "wrong_egress": "Routing, PBR or SD-WAN rule sends the flow elsewhere: correct with a change record.",
                "no_route": "Missing static route or a dynamic routing neighbour is down.",
            },
            "commands": [
                _c("fortios", "get system interface physical; get router info routing-table details <dest_ip>"),
                _c("panos", "show interface all; test routing fib-lookup virtual-router default ip <dest_ip>"),
                _c("cisco_asa", "show interface ip brief; show route <dest_ip>"),
                _c("cisco_ftd", "show interface ip brief; show route <dest_ip>"),
                _c("checkpoint", "clish: show interfaces all; show route destination <dest_ip>"),
                _c("junos_srx", "show interfaces terse; show route <dest_ip>"),
                _c("sonicwall", "Web UI: Network > Interfaces; Network > Routing"),
                _c("sophos", "Web UI: Configure > Network > Interfaces; Configure > Routing"),
            ],
        },
        {
            "order": 5,
            "action": "Test the security policy and trace the packet: ask the firewall which rule matches the flow, then run a packet trace/debug flow for that 5-tuple to see where it is allowed, translated or dropped.",
            "expected_result": "The intended allow rule matches and the trace shows the packet forwarded with the expected NAT.",
            "warnings": [
                "Debug flows and captures cost CPU: always filter on the flow and stop them when done.",
                "Check Point `fw ctl zdebug + drop` is busy on a loaded gateway: run it briefly.",
            ],
            "troubleshooting_matrix": {
                "deny_rule_matches": "Policy drop: identify the rule; a policy change needs a change record and approval.",
                "implicit_deny": "No rule matches: object, zone or application mismatch in the intended rule.",
                "drop_reason_reverse_path": "Anti-spoofing / RPF: asymmetric routing or wrong interface (step 4).",
                "allowed_but_fails": "The firewall forwards it: look downstream, at NAT (step 7) or at the security profile (step 9).",
            },
            "commands": [
                _c("fortios", "diagnose firewall iprope lookup <src_ip> <src_port> <dst_ip> <dst_port> <protocol> <in_interface>  (policy lookup; syntax varies by FortiOS version)"),
                _c("fortios", "diagnose debug flow filter addr <src_ip>; diagnose debug flow trace start 20; diagnose debug enable; (stop: diagnose debug disable)"),
                _c("fortios", "diagnose sniffer packet any 'host <src_ip> and port <port>' 4 0 l  (Ctrl+C to stop)"),
                _c("panos", "test security-policy-match from <src_zone> to <dst_zone> source <src_ip> destination <dst_ip> destination-port <port> protocol 6"),
                _c("cisco_asa", "packet-tracer input <in_if> tcp <src_ip> 1025 <dst_ip> <port> detailed"),
                _c("cisco_ftd", "packet-tracer input <in_if> tcp <src_ip> 1025 <dst_ip> <port> detailed"),
                _c("checkpoint", "expert: fw ctl zdebug + drop | grep <src_ip>  (short runs only)"),
                _c("junos_srx", "show security match-policies from-zone <src_zone> to-zone <dst_zone> source-ip <src_ip> destination-ip <dst_ip> source-port 1025 destination-port <port> protocol tcp"),
                _c("sonicwall", "Web UI: Packet Monitor with a filter on the source/destination IP (Monitor or Investigate menu by version)"),
                _c("sophos", "Web UI: Monitor & analyze > Diagnostics > Packet capture, filtered on the source IP"),
            ],
        },
        {
            "order": 6,
            "action": "Check the session (connection) table for the flow: does a session exist, in which state, with which NAT, and is it timing out early.",
            "expected_result": "An established session for the flow with expected translation and counters increasing in both directions.",
            "warnings": ["Clearing sessions drops live traffic: clear only the one flow, and only with approval."],
            "troubleshooting_matrix": {
                "no_session": "The first packet never arrives or is dropped before session creation: steps 4-5.",
                "one_way_counters": "Return traffic missing: asymmetric routing or the server does not reply.",
                "stale_session": "An old session with wrong NAT or route: clearing that single session (approved) may fix it.",
            },
            "commands": [
                _c("fortios", "diagnose sys session filter dst <dst_ip>; diagnose sys session list"),
                _c("panos", "show session all filter destination <dst_ip>; show session id <id>"),
                _c("cisco_asa", "show conn address <dst_ip> detail"),
                _c("cisco_ftd", "show conn address <dst_ip> detail"),
                _c("checkpoint", "expert: fw tab -t connections -s; fw ctl conntab -x <dst_ip> (version dependent)"),
                _c("junos_srx", "show security flow session destination-prefix <dst_ip>"),
                _c("sonicwall", "Web UI: Connections Monitor filtered on the IP"),
                _c("sophos", "Web UI: Monitor & analyze > Current activities > Live connections"),
            ],
        },
        {
            "order": 7,
            "action": "Check NAT for the flow: which source/destination NAT rule applies and what the translated addresses and ports are.",
            "expected_result": "The intended NAT rule applies and the translated address is the one the far side expects (for example the public IP an ISP or partner allow-lists).",
            "warnings": ["Overlapping or reordered NAT rules are a common cause after a change."],
            "troubleshooting_matrix": {
                "wrong_nat_rule": "Rule order or a broader rule above it: correct with a change record.",
                "no_nat": "Missing NAT for the egress interface or VIP.",
                "port_exhaustion": "Source NAT pool exhausted: add addresses or use per-interface PAT.",
            },
            "commands": [
                _c("fortios", "diagnose sys session list (look at hook=snat / dnat); show firewall vip"),
                _c("panos", "test nat-policy-match from <src_zone> to <dst_zone> source <src_ip> destination <dst_ip> destination-port <port> protocol 6"),
                _c("cisco_asa", "show nat detail; show xlate local <src_ip>"),
                _c("cisco_ftd", "show nat detail; show xlate local <src_ip>"),
                _c("checkpoint", "SmartConsole: NAT rulebase; expert: fw ctl zdebug + xlate (short runs only)"),
                _c("junos_srx", "show security nat source rule all; show security nat destination rule all"),
                _c("sonicwall", "Web UI: Policy > Rules and Policies > NAT Rules"),
                _c("sophos", "Web UI: Rules and policies > NAT rules"),
            ],
        },
        {
            "order": 8,
            "action": "If a VPN is involved, check the tunnel: phase 1 (IKE) and phase 2 (IPsec) up, the expected selectors/proxy IDs, counters increasing both ways, and recent rekey errors.",
            "expected_result": "IKE and IPsec SAs up for the expected subnets, encrypt and decrypt counters both increasing.",
            "warnings": ["Resetting a tunnel drops all traffic in it: coordinate with the far side first."],
            "troubleshooting_matrix": {
                "phase1_down": "Peer unreachable, PSK/certificate or IKE proposal mismatch: compare both ends.",
                "phase2_down_or_missing": "Selector/proxy-ID or PFS/proposal mismatch.",
                "encrypts_no_decrypts": "Far side not returning traffic: their routing/policy or NAT-T issue.",
            },
            "commands": [
                _c("fortios", "get vpn ipsec tunnel summary; diagnose vpn ike gateway list name <tunnel>"),
                _c("panos", "show vpn ike-sa gateway <gw>; show vpn ipsec-sa tunnel <tunnel>; show vpn flow"),
                _c("cisco_asa", "show crypto ikev2 sa; show crypto ipsec sa peer <peer_ip>"),
                _c("cisco_ftd", "show crypto ikev2 sa; show crypto ipsec sa peer <peer_ip>"),
                _c("checkpoint", "expert: vpn tu tlist; vpn tu (interactive)"),
                _c("junos_srx", "show security ike security-associations; show security ipsec security-associations"),
                _c("sonicwall", "Web UI: Network > IPSec VPN > Rules and Settings (tunnel status)"),
                _c("sophos", "Web UI: Configure > Site-to-site VPN (connection status)"),
            ],
        },
        {
            "order": 9,
            "action": "Check security profiles and logs for the flow: was it blocked or altered by IPS, antivirus, web/URL filtering, application control or SSL inspection? Search the traffic and threat logs for the source/destination in the problem window.",
            "expected_result": "No unexpected security-profile block for the flow; any block found is explained (true or false positive).",
            "warnings": ["A security-profile block is a SOC decision, not just a network fix: hand over to SOP-018 if it is an IPS/threat event."],
            "troubleshooting_matrix": {
                "ips_or_threat_block": "Triage the signature with SOP-018 before adding any exception.",
                "url_or_app_block": "Category or application mis-classification: request recategorisation or a scoped exception (approval).",
                "ssl_inspection_break": "Certificate pinning or untrusted CA: scoped SSL-inspection exemption (approval).",
            },
            "commands": [
                _c("fortios", "Web UI: Log & Report > Security Events and Forward Traffic, filtered on the IPs"),
                _c("panos", "show log threat direction equal both; show log traffic direction equal both (or Monitor > Logs)"),
                _c("cisco_asa", "show logging | include <src_ip>"),
                _c("cisco_ftd", "FMC: Analysis > Intrusions > Events and Analysis > Connections > Events"),
                _c("checkpoint", "SmartConsole: Logs & Monitor, filter src:<src_ip> dst:<dst_ip>"),
                _c("junos_srx", "show security idp attack table; show log messages | match <src_ip>"),
                _c("sonicwall", "Web UI: Logs (Monitor/Investigate), filtered on the IP; Security Services status"),
                _c("sophos", "Web UI: Monitor & analyze > Log viewer (Firewall, IPS, Web filter)"),
            ],
        },
        {
            "order": 10,
            "action": "Check licences and security signatures: support and threat subscriptions valid, and IPS/AV/URL databases updated recently. An expired subscription silently stops protection or updates.",
            "expected_result": "All subscriptions valid; signature databases updated within the vendor's normal interval (usually hours).",
            "warnings": ["The HSDEEP Firewalls page shows licence status from the adapters; confirm on the device when it says 'No data'."],
            "troubleshooting_matrix": {
                "expired": "Renew; until then protection or updates are off - note it on the incident as a risk.",
                "updates_failing": "Device cannot reach the update servers: DNS, routing or proxy to the vendor cloud.",
            },
            "commands": [
                _c("fortios", "get system fortiguard-service status; diagnose autoupdate versions"),
                _c("panos", "request license info; show system info | match threat-version"),
                _c("cisco_asa", "show version | include License"),
                _c("cisco_ftd", "FMC: System > Licenses > Smart Licenses; System > Updates"),
                _c("checkpoint", "cplic print"),
                _c("junos_srx", "show system license; show security idp security-package-version"),
                _c("sonicwall", "Web UI: Device > Settings > Licenses"),
                _c("sophos", "Web UI: Administration > Licensing"),
            ],
        },
        {
            "order": 11,
            "action": "Close out: confirm the flow works from the user's side, stop all debugs and captures, attach the evidence (policy match, trace, session, logs) to the incident and record the root cause and any change made.",
            "expected_result": "Flow verified, debugs stopped, root cause and evidence on the incident.",
            "warnings": ["Leaving a debug flow or capture running degrades the firewall."],
            "troubleshooting_matrix": {
                "change_was_needed": "Link the change record; schedule a review that the rule is scoped correctly.",
            },
            "commands": [
                _c("fortios", "diagnose debug disable; diagnose debug reset"),
                _c("cisco_asa", "no capture <name>"),
                _c("cisco_ftd", "no capture <name>"),
            ],
        },
    ],
    "possible_causes": [
        "Security policy deny or missing rule", "Wrong zone, object or application match", "NAT rule order or missing NAT",
        "Asymmetric routing / anti-spoofing drop", "Missing or wrong route, PBR or SD-WAN rule", "HA out of sync or unplanned failover",
        "VPN proposal, PSK or selector mismatch", "IPS/AV/URL/application-control block", "SSL inspection breaking the application",
        "Session table full or conserve mode", "Expired licence or stale signatures",
    ],
}


IPS_IDS_TRIAGE = {
    "sop_number": "SOP-018",
    "name": "IPS/IDS Alert Triage",
    "description": (
        "Triage an intrusion prevention or detection alert from a firewall IPS, network IDS or "
        "SIEM: validate it, decide true or false positive, scope the affected hosts, contain if "
        "needed, and tune without blinding the sensor."
    ),
    "category": "security",
    "tags": ["soc", "ips", "ids", "intrusion", "signature", "triage", "alert"],
    "required_skills": ["SOC Analyst"],
    "requires_approval": True,
    "estimated_resolution_minutes": 45,
    "rollback_procedure": "Containment blocks and signature exceptions are recorded with an expiry; remove them when the incident closes or the tuning review rejects them.",
    "steps": [
        {
            "order": 1,
            "action": "Read the alert fully: signature name and ID, CVE, severity, action taken (detected or blocked), source, destination, port, direction, and how many times it fired.",
            "expected_result": "You know what the signature detects and whether traffic was blocked or only seen.",
            "warnings": ["'Detected' (IDS / alert-only) means the traffic was allowed through."],
            "troubleshooting_matrix": {
                "blocked": "Less urgent for the target, but still confirm whether the attempt succeeded elsewhere.",
                "detected_only": "Treat as possibly successful: move quickly through steps 2-4.",
            },
            "commands": [
                _c("fortios", "Web UI: Log & Report > Security Events > Intrusion Prevention"),
                _c("panos", "show log threat direction equal both (or Monitor > Logs > Threat)"),
                _c("cisco_ftd", "FMC: Analysis > Intrusions > Events"),
                _c("checkpoint", "SmartConsole: Logs & Monitor, blade:IPS"),
                _c("junos_srx", "show security idp attack table"),
                _c("siem", "Search the signature ID and source IP for the last 7 days"),
            ],
        },
        {
            "order": 2,
            "action": "Validate against the target: is the destination host actually running the vulnerable product and version the signature targets, and is the service exposed?",
            "expected_result": "A clear yes/no on whether the target could be affected.",
            "warnings": ["Do not dismiss an alert as a false positive only because it fires often."],
            "troubleshooting_matrix": {
                "target_not_vulnerable": "Likely an unsuccessful attempt or false positive: go to step 5 to record and tune.",
                "target_vulnerable": "Possible compromise: go to step 3 and consider SOP-019.",
                "unknown_asset": "Find the owner and software inventory before deciding.",
            },
        },
        {
            "order": 3,
            "action": "Look for signs of success: responses from the target after the alert, new outbound connections from it, new processes, accounts or files on it (EDR), and the same source hitting other hosts.",
            "expected_result": "No follow-on activity from the target; the source is profiled (internal or external, reputation, other targets).",
            "troubleshooting_matrix": {
                "follow_on_activity": "Treat as a compromise: start SOP-019 (containment needs approval).",
                "source_hits_many_hosts": "Scan or campaign: consider blocking the source at the edge (approval).",
                "internal_source": "Internal host attacking others: likely compromised itself - SOP-019 on the source.",
            },
            "commands": [
                _c("siem", "All events for the target IP from the alert time onwards; outbound connections from the target"),
                _c("edr", "Process tree, network connections and new persistence on the target host"),
                _c("fortios", "diagnose sys session filter src <target_ip>; diagnose sys session list"),
                _c("panos", "show session all filter source <target_ip>"),
            ],
        },
        {
            "order": 4,
            "action": "Decide and act: classify true positive (attempt or success), benign true positive (expected traffic, for example a vulnerability scanner) or false positive. Containment (block the source, isolate the host, switch the signature to block) needs approval.",
            "expected_result": "A classification recorded on the incident and any containment approved and applied.",
            "warnings": ["Blocking a business partner's IP or a shared cloud range can cause an outage: scope the block."],
            "troubleshooting_matrix": {
                "true_positive_success": "Containment and SOP-019.",
                "true_positive_attempt": "Ensure prevention mode for the signature; patch the target if vulnerable.",
                "benign": "Document the source (scanner, monitoring) and exempt it narrowly.",
                "false_positive": "Tune in step 5.",
            },
        },
        {
            "order": 5,
            "action": "Tune without blinding the sensor: prefer an exception scoped to the exact signature AND source/destination over disabling the signature; give every exception an owner and a review date.",
            "expected_result": "Noise reduced, the signature still active for everyone else, the exception documented.",
            "warnings": ["Never disable a whole signature category to stop one false positive."],
            "troubleshooting_matrix": {
                "repeated_false_positives": "Raise with the vendor with packet captures; keep the scoped exception meanwhile.",
            },
        },
        {
            "order": 6,
            "action": "Close: confirm IPS is in the intended mode (prevent vs detect) on the policy that carried the traffic, signatures are current, and record the evidence, classification and actions on the incident.",
            "expected_result": "Incident closed with classification, evidence and any exception/containment listed with expiry.",
            "troubleshooting_matrix": {
                "signatures_outdated": "Fix updates (SOP-017 step 10) - detection gaps are a finding.",
            },
        },
    ],
    "possible_causes": [
        "External exploitation attempt", "Internal compromised host", "Vulnerability scanner or penetration test",
        "Signature false positive", "Misconfigured application producing exploit-like traffic",
    ],
}


SUSPECTED_COMPROMISE = {
    "sop_number": "SOP-019",
    "name": "Suspected Compromise - Investigation and Containment",
    "description": (
        "Incident response when a host or account is suspected compromised (successful exploit, "
        "malware, C2 beaconing, impossible travel). Preserve evidence, scope, contain with "
        "approval, eradicate, recover and report."
    ),
    "category": "security",
    "tags": ["soc", "incident-response", "compromise", "containment", "malware", "forensics"],
    "required_skills": ["SOC Analyst", "Incident Responder"],
    "requires_approval": True,
    "estimated_resolution_minutes": 240,
    "rollback_procedure": "Isolation, account disables and blocks are recorded on the incident and lifted only when eradication is confirmed.",
    "steps": [
        {
            "order": 1,
            "action": "Declare and assign: open (or raise) a security incident, name the incident lead, set severity, and start a timeline of every action with UTC times.",
            "expected_result": "One owner, one incident record, a running timeline.",
            "warnings": ["Do not tip off a potential insider: restrict the incident's visibility if needed."],
            "troubleshooting_matrix": {
                "regulated_data_possible": "Inform the data-protection/legal contact early: notification clocks may apply.",
            },
        },
        {
            "order": 2,
            "action": "Preserve evidence before changing anything: collect volatile data (running processes, network connections, logged-in users) with the EDR, snapshot or image the host where possible, and export the relevant firewall, proxy, DNS and authentication logs.",
            "expected_result": "Evidence collected and stored with hashes before containment.",
            "warnings": ["Rebooting or reimaging first destroys memory evidence."],
            "troubleshooting_matrix": {"no_edr": "Use OS tools (netstat, tasklist / ps) and copy output off the host."},
            "commands": [
                _c("edr", "Collect triage package / live response: processes, connections, autoruns"),
                _c("windows", "tasklist /v; netstat -ano; query user"),
                _c("linux", "ps auxf; ss -tupan; last -n 50"),
                _c("siem", "Export firewall, proxy, DNS and authentication events for the host and account"),
            ],
        },
        {
            "order": 3,
            "action": "Scope: which hosts, accounts and data are involved? Search for the same indicators (hashes, domains, IPs, user agents) across the estate, and the account's activity from the first suspicious event.",
            "expected_result": "A list of affected hosts and accounts with first-seen times.",
            "troubleshooting_matrix": {
                "indicators_on_many_hosts": "Widespread: escalate severity and consider network-level containment.",
                "privileged_account_involved": "Assume domain-level risk: plan credential resets for privileged accounts.",
            },
            "commands": [
                _c("siem", "Search indicators across all sources since the earliest event"),
                _c("fortios", "Web UI: FortiView > Sources / Destinations for the indicator IPs"),
                _c("panos", "show log traffic query equal \"( addr.dst in <ioc_ip> )\""),
            ],
        },
        {
            "order": 4,
            "action": "Contain (needs approval): isolate the host with the EDR or a switch-port/VLAN change, disable or reset the account and revoke its sessions and tokens, and block confirmed malicious IPs/domains at the firewall and DNS.",
            "expected_result": "No further attacker activity seen from the contained hosts and accounts.",
            "warnings": [
                "Containment is a change: record approval, time and exact scope.",
                "Isolating a critical server may cause an outage: agree the trade-off with the service owner.",
            ],
            "troubleshooting_matrix": {
                "activity_continues": "Missed a host or credential: back to step 3.",
            },
            "commands": [
                _c("edr", "Network-isolate the host"),
                _c("fortios", "Add the indicator to a blocked address group used by a deny policy (approved change)"),
                _c("panos", "Add to a dynamic address group / external dynamic list used by a deny rule (approved change)"),
            ],
        },
        {
            "order": 5,
            "action": "Eradicate and recover: remove the malware or persistence (or reimage), patch the exploited vulnerability, reset affected credentials, then return hosts to service under increased monitoring.",
            "expected_result": "Hosts clean, vulnerability closed, credentials rotated, monitoring in place.",
            "warnings": ["Restoring from backup? Make sure the backup predates the compromise."],
            "troubleshooting_matrix": {"reinfection": "Root cause not removed: re-scope before recovering again."},
        },
        {
            "order": 6,
            "action": "Report and learn: write the timeline, root cause, impact, data involved and actions; add new detections for what was missed; review whether notification duties apply.",
            "expected_result": "Report filed, detections added, follow-up actions owned.",
            "troubleshooting_matrix": {},
        },
    ],
    "possible_causes": [
        "Exploited unpatched vulnerability", "Phishing / malicious attachment", "Stolen or reused credentials",
        "Malicious insider", "Compromised third-party access",
    ],
}


BRUTE_FORCE = {
    "sop_number": "SOP-020",
    "name": "Brute-Force / Password-Spraying Investigation",
    "description": (
        "Investigate repeated authentication failures against VPN, firewall admin, SSO, mail or "
        "servers: tell brute force from password spraying, check whether any attempt succeeded, "
        "and block and harden."
    ),
    "category": "security",
    "tags": ["soc", "authentication", "brute-force", "password-spraying", "vpn", "login"],
    "required_skills": ["SOC Analyst"],
    "requires_approval": True,
    "estimated_resolution_minutes": 45,
    "rollback_procedure": "Source blocks are added with an expiry; account lockouts are released only after the user is verified.",
    "steps": [
        {
            "order": 1,
            "action": "Characterise the attack: number of failures, number of target accounts, number of sources, time pattern and the service targeted (VPN portal, firewall admin, SSO, mail, RDP/SSH).",
            "expected_result": "Classified as brute force (many tries, few accounts) or spraying (few tries, many accounts), with the source list.",
            "troubleshooting_matrix": {
                "many_accounts_few_tries": "Password spraying: likely aimed at weak common passwords - check successes across all accounts.",
                "one_account_many_tries": "Brute force or a misconfigured client with an old password: contact the account owner.",
            },
            "commands": [
                _c("fortios", "Web UI: Log & Report > Events > VPN Events / System Events (login failures)"),
                _c("panos", "show log system subtype equal globalprotect (or auth); Monitor > Logs > GlobalProtect"),
                _c("cisco_asa", "show logging | include 113015|611102  (AAA / user authentication failed)"),
                _c("siem", "Failed logins grouped by source IP and by account for the last 24 h"),
            ],
        },
        {
            "order": 2,
            "action": "Check for a successful login from the same sources or for the targeted accounts during or after the attack, including MFA prompts accepted unexpectedly.",
            "expected_result": "No success from attacking sources; no unexpected MFA approvals.",
            "warnings": ["A single success in a spraying run is a compromise until proven otherwise."],
            "troubleshooting_matrix": {
                "success_found": "Treat the account as compromised: SOP-019 (reset, revoke sessions, review activity).",
                "mfa_fatigue": "User approved an unexpected prompt: compromise - SOP-019.",
            },
        },
        {
            "order": 3,
            "action": "Block and slow down (needs approval): block the attacking sources at the edge or with geo/threat feeds, make sure lockout and rate limits apply to the targeted service, and require MFA where it is missing.",
            "expected_result": "Failures from the blocked sources stop; lockout and MFA policy confirmed on the service.",
            "warnings": ["Blocking cloud or ISP ranges can block real users: scope and give an expiry."],
            "troubleshooting_matrix": {
                "attack_moves_sources": "Distributed attack: rely on lockout/MFA and threat-feed blocks rather than single IPs.",
            },
        },
        {
            "order": 4,
            "action": "Close: reset passwords for accounts with weak or reused passwords found in the run, release lockouts after verifying users, record sources, accounts and actions on the incident.",
            "expected_result": "Accounts safe, users unlocked, incident documented.",
            "troubleshooting_matrix": {},
        },
    ],
    "possible_causes": ["Credential stuffing", "Password spraying campaign", "Targeted brute force", "Client with a stale saved password"],
}


MALICIOUS_OUTBOUND = {
    "sop_number": "SOP-021",
    "name": "Malicious Outbound Traffic / Threat-Intel Hit (C2, Malware Domain)",
    "description": (
        "A firewall, DNS filter or SIEM shows an internal host contacting a known-malicious IP or "
        "domain, or beaconing at regular intervals. Confirm, identify the process, decide whether "
        "it is a compromise, and contain."
    ),
    "category": "security",
    "tags": ["soc", "c2", "beaconing", "threat-intel", "dns", "malware", "outbound"],
    "required_skills": ["SOC Analyst"],
    "requires_approval": True,
    "estimated_resolution_minutes": 60,
    "rollback_procedure": "Blocks and isolations are recorded with an expiry and lifted after eradication is confirmed.",
    "steps": [
        {
            "order": 1,
            "action": "Confirm the hit: indicator, feed and its confidence/age, internal source host, destination, port, protocol, bytes sent and whether the connection was allowed or blocked.",
            "expected_result": "The internal host and the indicator confirmed; you know whether data left the network.",
            "warnings": ["Shared hosting and CDN IPs produce false hits: check the domain, not only the IP."],
            "troubleshooting_matrix": {
                "blocked_dns_or_url": "Lower urgency but the host still tried: identify the process (step 2).",
                "allowed_with_data": "Possible exfiltration: move to SOP-019 quickly.",
            },
            "commands": [
                _c("fortios", "Web UI: Log & Report > Security Events > DNS Query / Web Filter / Botnet C&C"),
                _c("panos", "show log threat direction equal both | match <indicator> (or Monitor > Logs > Threat)"),
                _c("siem", "All connections and DNS queries from the host to the indicator"),
            ],
        },
        {
            "order": 2,
            "action": "Identify the process and user on the host that made the connection, and check for regular-interval beaconing to the same or related destinations.",
            "expected_result": "The responsible process and user identified.",
            "troubleshooting_matrix": {
                "unknown_or_unsigned_process": "Likely malware: SOP-019.",
                "known_software": "Possible compromised update channel or a false feed entry: verify with the vendor/feed.",
                "regular_intervals": "Beaconing pattern: treat as C2 - SOP-019.",
            },
            "commands": [
                _c("edr", "Network events for the host filtered on the destination; parent process tree"),
                _c("windows", "netstat -ano | findstr <dest_ip>; tasklist /fi \"pid eq <pid>\""),
                _c("linux", "ss -tupan | grep <dest_ip>"),
            ],
        },
        {
            "order": 3,
            "action": "Contain (needs approval): block the indicator at the firewall and DNS for everyone, and isolate the host if the process is malicious.",
            "expected_result": "No further connections to the indicator from any host.",
            "warnings": ["Check the indicator is not a shared business service before a global block."],
            "troubleshooting_matrix": {
                "other_hosts_hit_same_indicator": "Widespread: SOP-019 scoping across all hosts.",
            },
        },
        {
            "order": 4,
            "action": "Close: record indicator, host, process, classification and actions; if it was a false feed entry, report it to the feed provider and add a scoped exception with a review date.",
            "expected_result": "Incident documented; any exception owned and time-limited.",
            "troubleshooting_matrix": {},
        },
    ],
    "possible_causes": ["Malware / C2 implant", "Potentially unwanted application", "Compromised software update", "Stale or wrong threat-intel entry"],
}


FORTIGATE_SCENARIOS = {
    "sop_number": "SOP-022",
    "name": "FortiGate Troubleshooting Scenarios (FortiOS)",
    "description": (
        "Scenario playbook for FortiGate firewalls, adapted from the HSDEEP FortiGate quick book: for "
        "each common symptom, what to check and which FortiOS commands show it. Use with SOP-017 (the "
        "vendor-neutral order). FortiOS commands, menus and feature support vary by release and "
        "model: validate on the target version first. Diagnostics are read-only; debug and sniffer "
        "output must be filtered narrowly and stopped after use."
    ),
    "category": "connectivity",
    "vendor": "Fortinet",
    "platform": "fortios",
    "device_type": "firewall",
    "tags": ["soc", "firewall", "fortigate", "fortios", "scenario", "vpn", "ha", "vip", "web-filter", "sd-wan"],
    "required_skills": ["Firewall Admin", "FortiGate"],
    "requires_approval": False,
    "estimated_resolution_minutes": 30,
    "rollback_procedure": (
        "Stop debugs (diagnose debug disable; diagnose debug reset) and sniffers. Clearing a "
        "session, failing over HA or changing a policy is a change: get approval, back up the "
        "configuration and state the rollback before doing it."
    ),
    "steps": [
        {
            "order": 1,
            "action": "Scenario: LAN can ping the gateway but not the internet. Check the default route, DNS, the outbound policy and its source NAT, SD-WAN health and rules, and the upstream gateway; confirm the traffic reaches the FortiGate and whether a reply comes back.",
            "expected_result": "Default route present, outbound policy matches with NAT enabled, SD-WAN members healthy, replies return.",
            "warnings": ["Filter flow debug on the client IP only."],
            "troubleshooting_matrix": {
                "no_default_route": "Static route or DHCP/PPPoE on the WAN: see SOP-016 step 4.",
                "policy_without_nat": "Enable NAT on the outbound policy (or central SNAT) with a change record.",
                "sdwan_member_dead": "Health check failing on the active link: check the link and the SLA target.",
                "no_reply": "Upstream or ISP problem: SOP-016 steps 5-8.",
            },
            "commands": [
                _c("fortios", "get router info routing-table all"),
                _c("fortios", "diagnose sys sdwan health-check  (older releases: diagnose sys virtual-wan-link health-check)"),
                _c("fortios", "diagnose debug flow filter addr <client_ip>; diagnose debug flow trace start 20; diagnose debug enable"),
                _c("fortios", "execute ping 8.8.8.8; execute ping example.com"),
            ],
        },
        {
            "order": 2,
            "action": "Scenario: one website is blocked. Find the session and the security-profile log for it: web filter category, application control, DNS filter, SSL inspection or certificate errors.",
            "expected_result": "The blocking profile and reason identified from the logs.",
            "warnings": ["An exception must be narrow (one URL or category for one policy) and authorised."],
            "troubleshooting_matrix": {
                "web_filter_category": "Re-rating request to FortiGuard or a scoped URL exception (approval).",
                "app_control": "Adjust the application sensor for that policy only (approval).",
                "ssl_certificate_error": "Deep inspection breaking the site: scoped SSL-inspection exemption (approval).",
                "no_log": "The profile may not log, or the traffic uses another policy: check the policy ID in the session.",
            },
            "commands": [
                _c("fortios", "Web UI: Log & Report > Security Events (Web Filter, Application Control, DNS Query)"),
                _c("fortios", "diagnose sys session filter dst <site_ip>; diagnose sys session list"),
            ],
        },
        {
            "order": 3,
            "action": "Scenario: VPN is up but a remote subnet is unreachable. Check the phase 2 selectors, routes in both directions, the policies for the tunnel interface, NAT exemption where needed, overlapping subnets and the tunnel counters; capture on both tunnel ends.",
            "expected_result": "Selectors include both subnets, routes point into the tunnel, encrypt and decrypt counters both increase.",
            "warnings": ["diagnose debug application ike -1 is very verbose: run it briefly and stop it."],
            "troubleshooting_matrix": {
                "selector_missing_subnet": "Add the subnet to phase 2 on both ends (change record).",
                "encrypt_no_decrypt": "Far side not returning traffic: their route, policy or selector.",
                "overlapping_subnets": "Needs NAT over the tunnel: a design change.",
            },
            "commands": [
                _c("fortios", "get vpn ipsec tunnel summary; diagnose vpn tunnel list name <tunnel>"),
                _c("fortios", "diagnose vpn ike gateway list name <tunnel>"),
                _c("fortios", "get router info routing-table details <remote_subnet_ip>"),
                _c("fortios", "diagnose sniffer packet any 'host <remote_host>' 4 0 l"),
            ],
        },
        {
            "order": 4,
            "action": "Scenario: after a policy change, traffic still follows the old behaviour. Existing sessions keep the policy they were created under: check the session's policy ID, the policy hit counters and the policy order.",
            "expected_result": "You can show which policy the live session uses and why.",
            "warnings": ["Clear only the specific session, with approval; never clear the whole session table on a production unit."],
            "troubleshooting_matrix": {
                "old_session_old_policy": "Clear that one session (approved) or wait for it to expire.",
                "higher_policy_matches": "Policy order: a broader rule above catches the traffic - reorder with a change record.",
            },
            "commands": [
                _c("fortios", "diagnose sys session filter src <src_ip>; diagnose sys session filter dst <dst_ip>; diagnose sys session list"),
                _c("fortios", "diagnose firewall iprope lookup <src_ip> <src_port> <dst_ip> <dst_port> <protocol> <in_interface>  (syntax varies)"),
                _c("fortios", "diagnose sys session clear  (only after a narrow filter is set, and only with approval)", disruptive=True),
            ],
        },
        {
            "order": 5,
            "action": "Scenario: an HA failover caused an outage. Check HA status and history, heartbeat links, monitored interfaces, configuration checksums and sync, routes, ARP/MAC convergence on the switches, session pickup and the event log; follow the incident and rollback process.",
            "expected_result": "The failover trigger identified and the cluster in sync with the intended primary.",
            "warnings": ["Do not fail back until the trigger is understood."],
            "troubleshooting_matrix": {
                "checksum_mismatch": "Configuration out of sync: find the differing table before syncing.",
                "monitored_interface_down": "Link monitor triggered it: fix the link first.",
                "split_brain": "Both units primary: heartbeat links failed - restore heartbeat before anything else.",
                "sessions_lost": "Session pickup off: drops on failover are expected; consider enabling it.",
            },
            "commands": [
                _c("fortios", "get system ha status; diagnose sys ha status"),
                _c("fortios", "diagnose sys ha checksum cluster"),
                _c("fortios", "Web UI: Log & Report > Events > HA Events"),
            ],
        },
        {
            "order": 6,
            "action": "Scenario: a published server (VIP / port forward) is unreachable from outside. Check the VIP mapping and external interface, the port forward, the inbound policy with the VIP as destination, the service, the route back, the server listening on the port and its host firewall, and any upstream NAT or ACL. Test from an external network.",
            "expected_result": "The inbound policy with the VIP matches, a session is created and the server replies.",
            "warnings": ["Testing from inside the LAN does not prove external access (hairpin NAT behaves differently)."],
            "troubleshooting_matrix": {
                "no_session": "Traffic not reaching the FortiGate, or no matching policy: upstream ACL/NAT or the policy.",
                "session_no_reply": "Server not listening, host firewall, or the server's default route is not the FortiGate.",
                "wrong_port_mapping": "Fix the VIP port forward (change record).",
            },
            "commands": [
                _c("fortios", "show firewall vip <vip_name>"),
                _c("fortios", "diagnose debug flow filter addr <external_test_ip>; diagnose debug flow trace start 20; diagnose debug enable"),
                _c("fortios", "diagnose sniffer packet any 'host <server_ip> and port <port>' 4 0 l"),
            ],
        },
        {
            "order": 7,
            "action": "Scenario: web filtering does not work. Check the profile is attached to the policy the traffic actually matches, the FortiGuard licence and rating lookups, flow vs proxy mode, DNS, and SSL inspection; read the web-filter logs.",
            "expected_result": "The policy carrying the traffic has the profile, ratings resolve, and the logs show filtering decisions.",
            "warnings": ["Without deep SSL inspection only the server name/certificate of HTTPS sites is visible."],
            "troubleshooting_matrix": {
                "profile_not_on_policy": "Traffic uses another policy, or the profile is not attached.",
                "rating_errors": "FortiGuard unreachable or the licence has expired: check service status.",
                "https_not_filtered": "Expected without deep inspection: decide on certificate vs deep inspection.",
            },
            "commands": [
                _c("fortios", "get system fortiguard-service status; diagnose debug rating"),
                _c("fortios", "Web UI: Log & Report > Security Events > Web Filter"),
            ],
        },
        {
            "order": 8,
            "action": "Scenario: high CPU or memory, or conserve mode. Check system performance, top processes, session count and rate, conserve-mode state, logging load and traffic spikes; correlate with recent changes and with FortiAnalyzer or HSDEEP monitoring.",
            "expected_result": "The process or traffic driving the load identified.",
            "warnings": ["Restarting a daemon or the unit drops traffic: approval and a maintenance window."],
            "troubleshooting_matrix": {
                "single_process_high": "A daemon is busy (for example IPS or proxy processes): check release notes and vendor support.",
                "session_flood": "Possible DoS or scan: find the top sources, then SOP-018.",
                "conserve_mode": "Memory exhausted: reduce load (logging, UTM) and investigate.",
            },
            "commands": [
                _c("fortios", "get system performance status; diagnose sys top 5 20"),
                _c("fortios", "diagnose hardware sysinfo conserve; get system session status"),
            ],
        },
        {
            "order": 9,
            "action": "Close every scenario the same way: change one thing at a time with approval, re-test, stop debugs and sniffers, and record timestamps, policy IDs, route results, logs and before/after tests on the incident.",
            "expected_result": "Evidence and rollback recorded; debugs stopped.",
            "warnings": ["Treat command examples as templates: replace interface names, IPs and objects; never paste example values into production."],
            "troubleshooting_matrix": {
                "change_made": "Link the change record and the before/after evidence.",
            },
            "commands": [_c("fortios", "diagnose debug disable; diagnose debug reset")],
        },
    ],
    "possible_causes": [
        "Missing default route or SNAT", "SD-WAN health-check failure", "Web filter / application control / SSL inspection block",
        "Phase 2 selector or route mismatch", "Existing session pinned to an old policy", "Policy order",
        "HA heartbeat, monitored interface or sync failure", "VIP mapping, policy or server-side issue",
        "FortiGuard licence or rating failure", "Process load, session flood or conserve mode",
    ],
}

SOC_SEEDS = [FIREWALL_DIAGNOSTIC, IPS_IDS_TRIAGE, SUSPECTED_COMPROMISE, BRUTE_FORCE, MALICIOUS_OUTBOUND,
             FORTIGATE_SCENARIOS]

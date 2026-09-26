"""
AI agent catalogue (Section 18: AI-Native Infrastructure Intelligence).

Pure, dependency-free metadata for the specialized HSDEEP CORE AI agents.
Each agent is a *persona* with a name, responsibilities, permission boundary,
approval requirements, tool access, retry/timeout policy and escalation rules
— the fields the ``agents`` table seeds and the execution layer enforces.

Design constraint (from ``app/models/agent.py``): agents share a single AI
infrastructure layer, NOT separate LLM instances. This module only defines the
*catalogue*; the ORM ``Agent`` model persists it and ``TaskService`` /
``AutomationRunner`` execute it with approval + audit.

No destructive change runs without explicit policy + approval: agents with
``can_approve=False`` always defer to a human/escalation path.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentSpec:
    agent_id: str
    name: str
    role: str
    department: str
    level: str
    responsibilities: tuple[str, ...] = ()
    permission_boundary: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    approval_required: bool = False
    can_approve: bool = False
    escalation_authority: str = "none"
    retry_policy: str = "3x-exponential-backoff"
    timeout_seconds: int = 60
    idempotency_protected: bool = True
    audit: bool = True
    human_escalation: bool = True


AGENT_CATALOGUE: tuple[AgentSpec, ...] = (
    AgentSpec(
        "discovery", "Discovery Agent", "Discovery", "Infrastructure", "analyst",
        responsibilities=("Discover devices via SNMP/SSH/API/cloud",
                          "Upsert devices idempotently", "Resolve site/zone identity",
                          "Report reachability"),
        permission_boundary=("devices.read", "devices.write", "monitoring_sources.read"),
        tools=("snmp", "ssh", "cloud-api", "lldp", "cdp"),
        retry_policy="3x-exponential-backoff", timeout_seconds=180,
    ),
    AgentSpec(
        "topology", "Topology Agent", "Topology", "Infrastructure", "analyst",
        responsibilities=("Build CE/PE/P + MPLS/OSPF/VPN model",
                          "Persist TopologyLink edges", "Detect redundant paths",
                          "Track topology changes"),
        permission_boundary=("topology.read", "topology.write"),
        tools=("topology", "lldp", "cdp", "routing-tables", "bgp", "ospf"),
    ),
    AgentSpec(
        "network_ops", "Network Operations Agent", "Network Ops", "NOC", "lead",
        responsibilities=("Analyze OSPF/BGP/MPLS path issues", "Detect neighbor failures",
                          "Recommend routing remediation", "Trace paths"),
        permission_boundary=("network.read", "network.write"),
        tools=("path-trace", "ospf", "bgp", "mpls", "ping", "traceroute"),
    ),
    AgentSpec(
        "security_ops", "Security Operations Agent", "Security Ops", "SOC", "lead",
        responsibilities=("Correlate security events", "Analyze firewall policy",
                          "Detect anomalies", "Recommend hardening"),
        permission_boundary=("security.read", "security.write"),
        tools=("firewall", "ids-ips", "siem", "threat-intel"), human_escalation=True,
    ),
    AgentSpec(
        "monitoring", "Monitoring Agent", "Monitoring", "NOC", "analyst",
        responsibilities=("Schedule collection runs", "Track source health",
                          "Detect metric anomalies", "Threshold alerts"),
        permission_boundary=("monitoring.read", "monitoring.write"),
        tools=("collector", "telemetry", "metrics"),
    ),
    AgentSpec(
        "incident", "Incident Agent", "Incident", "NOC", "lead",
        responsibilities=("Create/dedup/prioritize incidents", "Assign + escalate",
                          "Track SLA", "Recommend remediations"),
        permission_boundary=("incidents.read", "incidents.write", "tickets.write"),
        tools=("incident", "sla", "priority", "escalation"),
    ),
    AgentSpec(
        "change", "Change Management Agent", "Change Management", "Operations", "lead",
        responsibilities=("Assess change risk", "Schedule changes", "Require approval",
                          "Track post-change health"),
        permission_boundary=("changes.read", "changes.write"),
        tools=("change-request", "risk", "maintenance-window"),
        approval_required=False, can_approve=False, human_escalation=True,
    ),
    AgentSpec(
        "capacity", "Capacity Agent", "Capacity", "Operations", "analyst",
        responsibilities=("Forecast capacity/performance", "Detect saturation",
                          "Recommend scaling", "Trend analysis"),
        permission_boundary=("metrics.read", "reports.read"),
        tools=("metrics", "forecast"),
    ),
    AgentSpec(
        "compliance", "Compliance Agent", "Compliance", "Governance", "lead",
        responsibilities=("Check config drift", "Audit changes", "Enforce policy",
                          "Produce audit reports"),
        permission_boundary=("audit.read", "config.read"),
        tools=("audit-log", "config-diff", "reports"),
    ),
    AgentSpec(
        "automation", "Automation Agent", "Automation", "Operations", "lead",
        responsibilities=("Execute runbooks/SOPs", "Enforce idempotency",
                          "Gate high-risk actions", "Record executions"),
        permission_boundary=("automation.read", "automation.write"),
        tools=("runbook", "sop", "ssh", "rest", "api"),
        approval_required=True, can_approve=False, human_escalation=True,
    ),
    AgentSpec(
        "knowledge", "Knowledge Agent", "Knowledge", "Operations", "analyst",
        responsibilities=("Maintain knowledge articles", "Suggest SOPs",
                          "Answer operational questions"),
        permission_boundary=("knowledge.read", "knowledge.write"),
        tools=("knowledge", "sop"),
    ),
    AgentSpec(
        "ai_cto", "AI CTO Agent", "AI CTO", "Executive", "director",
        responsibilities=("Coordinate agents", "Prioritize cross-domain work",
                          "Synthesize executive status", "Approve within limit"),
        permission_boundary=("all.read", "orchestration"),
        tools=("orchestrator", "reporting"),
        can_approve=True, escalation_authority="medium",
    ),
    AgentSpec(
        "service_impact", "Service Impact Agent", "Service Impact", "Operations", "lead",
        responsibilities=("Compute dependency-aware impact", "Blast-radius analysis",
                          "Prioritize incidents by service impact",
                          "Map impacted services"),
        permission_boundary=("topology.read", "business_service.read", "incidents.read"),
        tools=("topology", "dependency-graph", "business-service"),
    ),
)

AGENTS_BY_ID: dict[str, AgentSpec] = {a.agent_id: a for a in AGENT_CATALOGUE}


def get_agent(agent_id: str | None) -> AgentSpec | None:
    if not agent_id:
        return None
    return AGENTS_BY_ID.get(agent_id.strip().lower())


def agent_requires_approval(agent_id: str | None) -> bool:
    """Whether the agent's high-risk work requires explicit approval."""
    agent = get_agent(agent_id)
    if agent is None:
        return True  # unknown agents are locked down by default
    return agent.approval_required or not agent.can_approve


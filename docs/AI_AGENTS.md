# AI Agents (Section 18)

## Multi-agent orchestration

HSDEEP CORE AI uses AI + multi-agent orchestration for: topology
interpretation, anomaly detection, alert correlation & de-duplication,
root-cause analysis, service-impact analysis, dependency-aware prioritization,
capacity/performance forecasting, config-drift detection, change-risk analysis,
network path / OSPF-neighbor / BGP / MPLS / firewall-policy analysis,
security-event correlation, recommended remediation, and automated runbook
execution — always gated so that **high-risk changes require human approval**.

## Agent catalogue

Persisted by the `agents` model and defined (as metadata) in
`backend/app/core/ai_agents.py`:

| agent_id        | role           | responsibility summary                       |
|-----------------|----------------|-----------------------------------------------|
| `discovery`     | Analyst        | Discover/upsert devices, resolve identity     |
| `topology`      | Analyst        | Build CE/PE/P + MPLS/OSPF/VPN model, edges    |
| `network_ops`   | Lead           | OSPF/BGP/MPLS path & routing analysis         |
| `security_ops`  | Lead           | Security-event correlation, firewall policy   |
| `monitoring`    | Analyst        | Collection runs, source health, anomalies     |
| `incident`      | Lead           | Create/dedup/prioritize/assign/escalate       |
| `change`        | Lead           | Change risk, scheduling, approval             |
| `capacity`      | Analyst        | Capacity/performance forecasting              |
| `compliance`    | Lead           | Config drift, audit, policy enforcement       |
| `automation`    | Lead           | Runbooks/SOPs, idempotency, high-risk gating  |
| `knowledge`     | Analyst        | Knowledge articles, SOP suggestions           |
| `ai_cto`        | Director       | Cross-domain coordination, limited approval   |
| `service_impact`| Lead           | Dependency-aware impact & blast-radius        |

## Agent guarantees (enforced by design)

- Clear **responsibilities** and **permission boundaries**
- **Tenant context** and **audit records** (all specs have `audit=True`)
- **Tool access controls**
- **Approval requirements** — `approval_required` / `can_approve`;
  agents that cannot approve always defer to human/escalation
- **Failure** & **timeout** handling, **retry policies**
- **Idempotency protection** (`idempotency_protected=True`)
- **Human escalation** (`human_escalation=True`)

## Safety rule

AI never automatically executes destructive infrastructure changes without
explicit policy and approval. In code: `agent_requires_approval()` returns
`True` for any agent that cannot approve, and **`True` for unknown agents
(locked down by default)**.

## Tests

`tests/test_ai_agents.py` validates the catalogue, boundaries, and the
approval-safety rule.
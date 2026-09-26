"""Pure unit tests for the AI agent catalogue (Section 18)."""

from __future__ import annotations

from app.core.ai_agents import (
    AGENT_CATALOGUE,
    agent_requires_approval,
    get_agent,
)


def test_catalogue_has_all_specialized_agents():
    ids = {a.agent_id for a in AGENT_CATALOGUE}
    expected = {
        "discovery", "topology", "network_ops", "security_ops", "monitoring",
        "incident", "change", "capacity", "compliance", "automation",
        "knowledge", "ai_cto", "service_impact",
    }
    assert expected <= ids


def test_get_agent():
    assert get_agent("topology") is not None
    assert get_agent("TOPOLOGY") is not None  # case-insensitive
    assert get_agent("nope") is None
    assert get_agent(None) is None


def test_agents_have_responsibilities_and_boundaries():
    for agent in AGENT_CATALOGUE:
        assert agent.responsibilities
        assert agent.permission_boundary
        assert agent.audit is True
        assert agent.idempotency_protected is True


def test_automation_agent_requires_approval():
    # High-risk remediation must always require approval.
    assert get_agent("automation").approval_required is True
    assert agent_requires_approval("automation") is True


def test_change_agent_requires_approval_even_if_not_flagged():
    # can_approve=False => locked-down by default.
    assert agent_requires_approval("change") is True


def test_discovery_does_require_approval():
    # An agent that cannot approve defers to human approval by default.
    assert get_agent("discovery").can_approve is False
    assert agent_requires_approval("discovery") is True


def test_unknown_agent_locked_down():
    assert agent_requires_approval("unknown_agent") is True


def test_ai_cto_can_approve_within_limit():
    cto = get_agent("ai_cto")
    assert cto.can_approve is True
    assert cto.escalation_authority == "medium"

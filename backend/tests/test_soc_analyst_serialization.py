"""
SOC analyst roster serialization (GET /api/soc/analysts).

Regression guard: the route built its payload with ``AgentRead.model_dump(orm_row)``,
which calls the Pydantic method unbound on an ORM object and raised
``AttributeError: 'Agent' object has no attribute '__pydantic_serializer__'`` -
every roster request returned 500 (rendered as "SOC roster unavailable").
The ORM row must go through ``model_validate`` (from_attributes) first.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from app.api.routes.soc import _analyst_to_out


def _agent_row(**overrides):
    now = datetime.now(timezone.utc)
    row = dict(
        id=uuid.uuid4(), tenant_id=uuid.uuid4(), agent_id="SOC-L1-001", name="Analyst One",
        role="soc_analyst", department="soc", level="l1", description=None, avatar_url=None,
        skills=[], capabilities=[], supported_task_types=[], permissions=[], max_workload=5,
        config={}, manager_id=None, status="active", availability="available", is_active=True,
        current_workload=0, tasks_completed=3, tasks_failed=0, avg_resolution_minutes=None,
        success_rate=1.0, last_active_at=None, created_at=now, updated_at=now,
        is_available_for_work=True, can_accept_task=True,
    )
    row.update(overrides)
    # Attribute access only, exactly like an ORM instance.
    return SimpleNamespace(**row)


def test_orm_style_row_serializes_with_event_counts():
    row = _agent_row()
    out = _analyst_to_out(row, {"open": 2, "acknowledged": 1, "total": 7, "resolved": 4})
    assert out.id == row.id and out.name == "Analyst One"
    assert (out.open_noc_events, out.acknowledged_noc_events, out.total_assigned_events) == (2, 1, 7)
    assert out.resolved_today == 4


def test_missing_counts_are_zero_not_an_error():
    out = _analyst_to_out(_agent_row(), None)
    assert (out.open_noc_events, out.acknowledged_noc_events, out.total_assigned_events) == (0, 0, 0)

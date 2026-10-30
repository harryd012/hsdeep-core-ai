"""Pydantic schemas for the AI Operations Command Center aggregate read API.

These are plain serialization DTOs (no `from_attributes`) because the
Command Center overview is assembled from several real repositories by
the service layer, matching the convention used by schemas/dashboard.py.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

AgentStatus = Literal[
    "active",
    "inactive",
]
AgentAvailability = Literal[
    "available",
    "busy",
    "away",
    "offline",
]
TaskStatus = Literal[
    "created",
    "assigned",
    "accepted",
    "investigating",
    "executing",
    "verifying",
    "verified",
    "recorded",
    "failed",
    "retrying",
    "escalated",
    "reassigned",
    "cancelled",
]


class CCAgent(BaseModel):
    """A flattened, serializable view of one Agent for the Command Center."""

    id: uuid.UUID
    agent_id: str
    name: str
    role: str
    department: str
    level: str
    manager_id: uuid.UUID | None = None
    status: str
    availability: str
    is_active: bool
    current_workload: int
    max_workload: int
    skills: list[str] = []
    supported_task_types: list[str] = []
    success_rate: float


class CCTreeNode(BaseModel):
    """Hierarchy node — reuses the existing agent org-tree shape (edges'levels,
    reports, etc). Mirrors AgentService.get_organization_tree() output"""

    id: uuid.UUID
    agent_id: str
    name: str
    role: str
    department: str
    level: str
    status: str
    availability: str
    current_workload: int
    max_workload: int
    is_available: bool
    success_rate: float
    skills: list[str] = []
    children: list["CCTreeNode"] = []


CCTreeNode.model_rebuild()


class CCTask(BaseModel):
    """A serializable view of one Task for the Command Center queue."""

    id: uuid.UUID
    task_number: str
    title: str
    task_type: str
    priority: str
    status: str
    assigned_agent_id: uuid.UUID | None = None
    assigned_agent_name: str | None = None
    manager_agent_id: uuid.UUID | None = None
    manager_agent_name: str | None = None
    department: str | None = None  # denormalized from the assigned agent
    incident_id: uuid.UUID | None = None
    execution_result: dict = {}
    evidence: dict = {}
    verified: bool = False
    created_at: datetime | None = None
    assigned_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


class CCWorkload(BaseModel):
    """Per-department operational workload (derived from real agents/tasks).

    ``total_tasks`` is every task assigned to a department agent regardless of
    status; ``active_tasks`` is the non-terminal subset (not ``recorded`` or
    ``cancelled``). Keeping both lets the UI label the two distinctly.
    """

    department: str
    agent_count: int
    active_agents: int
    total_tasks: int = 0
    active_tasks: int
    queued_tasks: int
    verifying_tasks: int
    completed_tasks: int
    critical_tasks: int


class CCActivityEvent(BaseModel):
    """A real event drawn from a Task's audit_log (never fabricated)."""

    task_id: uuid.UUID
    task_number: str
    timestamp: str  # caller-serialized ISO string; kept str so frontend can render directly
    action: str
    from_status: str | None = None
    to_status: str | None = None
    actor: str | None = None
    message: str | None = None


class CCMetrics(BaseModel):
    """Top-bar counters — all derived from real Agent/Task rows."""

    total_agents: int = 0
    active_agents: int = 0
    assigned_agents: int = 0
    working_agents: int = 0
    total_tasks: int = 0  # non-terminal tasks
    queued: int = 0
    working: int = 0
    verifying: int = 0
    completed: int = 0
    failed: int = 0
    critical: int = 0  # P1 non-terminal tasks


class CommandCenterHealth(BaseModel):
    """Real dependency health — derived from live probes, never hardcoded.

    Fields mirror the ``/health`` endpoint so the frontend has a single
    authoritative contract for system state derivation.
    """

    status: str  # 'ok' | 'degraded' | 'unavailable'
    ready: bool
    ai_ready: bool
    system_online: bool
    timestamp: str
    dependencies: dict[str, str]  # e.g. {"database": "up", "redis": "up", "ollama": "up"}


class CommandCenterOverview(BaseModel):
    """The full Command Center read payload."""

    generated_at: datetime
    metrics: CCMetrics
    tree: list[CCTreeNode]
    agents: list[CCAgent]
    tasks: list[CCTask]
    workload: list[CCWorkload]
    activity: list[CCActivityEvent]
    system_online: bool = False
    health: CommandCenterHealth | None = None


class CommandIntake(BaseModel):
    """Payload for creating+routing real work from an owner command.



    The backend selects an enterprise operations department from the
    command text deterministically (like the existing legacy ROUTING_RULES
    heuristic, but implemented here against the real agent DB) then creates a
    real Task via TaskService and auto-routes it to a real agent via the
    existing RoutingEngine. No simulated tasks are ever created."""

    command: str
    command_source: str = "command_center"
    title: str | None = None
    priority: str = "P2"
    incident_id: uuid.UUID | None = None
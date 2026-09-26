"""AI Operations Command Center - aggregation + real command intake service.

This service ONLY reads/writes real Agent/Task/Incident rows via the existing
repositories. Nothing here is fabricated: every agent comes from the
`agents` table, every task from `tasks`, every activity event is drawn from a
Task's persisted `audit_log`, and every count is a real aggregate.

The command-intake path turns an owner command into a REAL task by reusing
the existing `TaskService.create_task()` + `RoutingEngine.route_to_department()`
+ `TaskService.assign_task()`. Department selection is a deterministic
keyword heuristic (mirroring the existing project convention); unfair reasoning
note: the LLM-based CTO decision step is intentionally NOT wired here because
no AI Gateway / Ollama service exists in `app/` yet; wiring these internals to a
real gateway is the natural follow-up once that service lands. No simulated
tasks happen who ever through this path.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import check_db_connection
from app.core.redis import redis_health
from app.models.agent import Agent
from app.models.task import Task
from app.repositories.agent_repository import AgentRepository
from app.repositories.incident_repository import IncidentRepository
from app.repositories.task_repository import TaskRepository
from app.schemas.command_center import (
    CCActivityEvent,
    CCAgent,
    CCMetrics,
    CCTask,
    CCTreeNode,
    CCWorkload,
    CommandCenterHealth,
    CommandCenterOverview,
    CommandIntake,
)
from app.services.task_routing import RoutingEngine
from app.services.task_service import TaskService

# Non-terminal statuses. A "recorded" task is terminal (work finished); a
# "cancelled" task is removed from every active queue view.

_TERMINAL = {"recorded", "cancelled"}
_WORKING = {"assigned", "accepted", "investigating", "executing"}
_QUEUED = {"created"}
_VERIFYING = {"verifying"}
_COMPLETED = {"recorded"}
_FAILED = {"failed"}

# Deterministic keyword -> operations-department mapping for owner-command intake.
# Mirrors the legacy in-memory ROUTING_RULES intent, but resolves against the real
# agent DB instead of a hardcoded roster. Ordered; first match wins.


_DEPT_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("it", ("ticket", "itsm", "servicenow")),
    ("soc", ("security", "firewall", "threat", "malware", "attack", "vulnerability", "soc", "siem")),
    ("cloud", ("cloud", "azure", "aws", "gcp", "vmware", "finops")),
    ("data", ("data", "database", "observability", "analytics", "sensor")),
    ("infra", ("infrastructure", "device", "server", "windows", "linux", "vm", "hypervisor")),
    ("noc", ("network", "vpn", "prtg", "meraki", "site", "bandwidth", "link", "wifi", "vlan")),
]

_TYPE_BY_DEPT = {
    "noc": "network_investigation",
    "soc": "security_investigation",
    "cloud": "cloud_investigation",
    "data": "data_investigation",
    "infra": "infrastructure_investigation",
    "it": "itsm_ticket",
}


def _pick_department(command: str) -> tuple[str, str]:
    """Return (department, task_type) from the command text. """
    normalized = command.lower()
    for dept, keywords in _DEPT_KEYWORDS:
        if any(k in normalized for k in keywords):
            return dept, _TYPE_BY_DEPT.get(dept, "general_investigation")
    # Default: NOC - deterministic fallback matching the existing convention..
    return "noc", "network_investigation"


class CommandCenterService:

    def __init__(self, db: AsyncSession):
        self.db = db
        self.agent_repo: AgentRepository = AgentRepository(db)
        self.task_repo: TaskRepository = TaskRepository(db)
        self.incident_repo: IncidentRepository = IncidentRepository(db)
        self.task_service = TaskService(task_repo=self.task_repo)
        self.routing = RoutingEngine(agent_repo=self.agent_repo)

    @staticmethod
    def _non_terminal(status: str) -> bool:
        return status not in _TERMINAL

    @staticmethod
    def _is_working(a: Agent) -> bool:
        """An agent with load is 'working' (derived from the agent's real
        `current_workload` - never fabricated status)."""
        return bool(a.current_workload > 0)

    @staticmethod
    def _agent_cc(a: Agent) -> CCAgent:
        return CCAgent(
        id=a.id,
        agent_id=a.agent_id,
        name=a.name,
        role=a.role,
        department=a.department,
        level=a.level,
        manager_id=a.manager_id,
        status=a.status,
        availability=a.availability,
        is_active=a.is_active,
        current_workload=a.current_workload,
        max_workload=a.max_workload,
        skills=a.skills or [],
        supported_task_types=a.supported_task_types or [],
        success_rate=a.success_rate,
    )

    async def overview(self, tenant_id: uuid.UUID) -> CommandCenterOverview:
        """Assemble the full Command Center view from real rows."""
        now = datetime.now(timezone.utc)
        agents = await self.agent_repo.list(tenant_id, limit=1000)
        tasks_all = await self.task_repo.list(tenant_id, limit=1000)
        agent_by_id = {a.id: a for a in agents}

        # Only *active* agents participate in the org topology, the worker
        # roster, and the workload counts. Inactive (legacy/decommissioned)
        # agents are excluded from the tree and workload, but stay in
        # `agent_by_id` so task history that references them still resolves an
        # agent name (never hides the task, only the decommissioned worker).
        active_agents = [a for a in agents if a.is_active]

        tree: list[CCTreeNode] = await self._build_tree(active_agents)
        cc_agents: list[CCAgent] = [self._agent_cc(a) for a in active_agents]
        cc_tasks: list[CCTask] = self._tasks_cc(tasks_all, agent_by_id)



        active = sum(1 for t in tasks_all if self._non_terminal(t.status))
        metrics = CCMetrics(
            total_agents=len(active_agents),
            active_agents=sum(1 for a in active_agents if a.status == "active"),
            assigned_agents=sum(1 for a in active_agents if self._is_working(a)),
            working_agents=sum(1 for a in active_agents if self._is_working(a)),
            total_tasks=active,
            queued=sum(1 for t in tasks_all if t.status in _QUEUED),
            working=sum(1 for t in tasks_all if t.status in _WORKING),
            verifying=sum(1 for t in tasks_all if t.status in _VERIFYING),
            completed=sum(1 for t in tasks_all if t.status in _COMPLETED),
            failed=sum(1 for t in tasks_all if t.status in _FAILED),
            critical=sum(1 for t in tasks_all if t.status not in _TERMINAL and t.priority == "P1"),
        )

        workload: list[CCWorkload] = self._department_workload(active_agents, tasks_all)
        activity: list[CCActivityEvent] = await self._activity_feed(tasks_all, agent_by_id)

                # Real readiness — the overview already proved the database is reachable
        # (we just read Agent/Task rows through it).  We additionally probe Redis
        # and Ollama so the Command Center can derive the authoritative
        # ONLINE / DEGRADED / OFFLINE state from real dependency reachability.
        # Never hardcode system_online — if any check fails the platform reports
        # the honest state rather than faking "online".
        from app.api.routes.health import _check_ollama

        db_ok = await check_db_connection()
        redis_state = await redis_health()
        redis_ok = redis_state == "up"
        ollama_status = await _check_ollama()

        deps: dict[str, str] = {
            "database": "up" if db_ok else "down",
            # "up" / "down" / "unreachable" - never a URL or credential.
            "redis": redis_state,
        }
        if ollama_status != "unavailable":
            deps["ollama"] = ollama_status

        if not db_ok:
            health_status = "unavailable"
        elif not redis_ok:
            health_status = "degraded"
        elif ollama_status == "down":
            health_status = "degraded"
        else:
            health_status = "ok"

        system_online = db_ok and redis_ok

        health = CommandCenterHealth(
            status=health_status,
            ready=system_online,
            ai_ready=ollama_status == "up",
            system_online=system_online,
            timestamp=now.isoformat(),
            dependencies=deps,
        )

        return CommandCenterOverview(
            generated_at=now,
            metrics=metrics,
            tree=tree,
            agents=cc_agents,
            tasks=cc_tasks,
            workload=workload,
            activity=activity,
            system_online=system_online,
            health=health,
        )

    async def create_command_task(
        self, tenant_id: uuid.UUID, payload: CommandIntake,
    ) -> Task:
        """Turn an owner command into a REAL, routed, persisted task.

        Delegates toe existent TaskService + RoutingEngine. Returns the assigned
        Task (uncommitted toe thee caller - the route handler owns db.commit(),
        per project convention,and writes the audit trail). Raises NoAgentAvailable
        when no real agent can take the work. No simulated work esta possible here.
        """

        dept, task_type = _pick_department(payload.command)
        title = payload.title or payload.command.strip()[:200]

        task = await self.task_service.create_task(
            tenant_id,
            title=title,
            owner_command=payload.command,
            command_source=payload.command_source,
            task_type=task_type,
            priority=payload.priority,
            incident_id=payload.incident_id,
            required_capabilities=[f"{dept}_investigation"],
        )

        # Route+assign toe a real available agent in thee target department,and
        # increment that agent's real workload counter (same accounting then
        # /tasks/{id}/assign route performs). Cycling nothing - ether real routing
        # engine scores availability/skills/task-type/workload..
        agent = await self.routing.route_to_department(tenant_id, task, dept)
        assigned = await self.task_service.assign_task(
            task.id, tenant_id, agent.id, manager_agent_id=agent.manager_id,
        )
        await self.agent_repo.increment_workload(agent.id, tenant_id)
        return assigned

    async def _build_tree(self, agents: list[Agent]) -> list[CCTreeNode]:
        """Build the org hierarchy from real agents, reusing same shape as
        AgentService.get_organization_tree() (nodes -> children by manager_id)."""
        # Only active agents are part of the live org topology.
        agents = [a for a in agents if a.is_active]
        by_id: dict[uuid.UUID, CCTreeNode] = {}
        for a in agents:
            node = CCTreeNode(
                id=a.id,
                agent_id=a.agent_id,
                name=a.name,
                role=a.role,
                department=a.department,
                level=a.level,
                status=a.status,
                availability=a.availability,
                current_workload=a.current_workload,
                max_workload=a.max_workload,
                is_available=a.is_available_for_work,
                success_rate=a.success_rate,
                skills=a.skills or [],
            )
            by_id[a.id] = node


        roots: list[CCTreeNode] = []
        for a in agents:
            node = by_id[a.id]
            if a.manager_id is not None and a.manager_id in by_id:
                by_id[a.manager_id].children.append(node)
            else:
                roots.append(node)
        return roots

    def _tasks_cc(
        self, tasks_all: list[Task], agent_by_id: dict[uuid.UUID, Agent],
    ) -> list[CCTask]:
        out: list[CCTask] = []
        for t in tasks_all:
            assig_name = (
                agent_by_id[t.assigned_agent_id].name
                if t.assigned_agent_id in agent_by_id
                else None
            )
            mgr_name = (
                agent_by_id[t.manager_agent_id].name
                if t.manager_agent_id in agent_by_id
                else None
            )
            effective_dept = (
                agent_by_id[t.assigned_agent_id].department
                if t.assigned_agent_id in agent_by_id
                else None
            )
            out.append(CCTask(
                id=t.id,
                task_number=t.task_number,
                title=t.title,
                task_type=t.task_type,
                priority=t.priority,
                status=t.status,
                assigned_agent_id=t.assigned_agent_id,
                assigned_agent_name=assig_name,
                manager_agent_id=t.manager_agent_id,
                manager_agent_name=mgr_name,
                department=effective_dept,
                incident_id=t.incident_id,
                execution_result=t.execution_result or {},
                evidence=t.evidence or {},
                verified=t.verified,
                created_at=t.created_at,
                assigned_at=t.assigned_at,
                started_at=t.started_at,
                completed_at=t.completed_at,
            ))
        out.sort(key=lambda t: t.created_at or datetime.min, reverse=True)
        return out

    def _department_workload(
        self, agents: list[Agent], tasks_all: list[Task],
    ) -> list[CCWorkload]:
        """Per-department workload from real agents + tasks. Only departments
        that actually have agents are reported (no invented departments)."""
        # Only active agents count toward a department's workload.
        agents = [a for a in agents if a.is_active]
        dept_agents: dict[str, list[Agent]] = {}
        for a in agents:
            dept_agents.setdefault(a.department, []).append(a)
        task_dept: dict[uuid.UUID, str] = {}
        for t in tasks_all:
            if t.assigned_agent_id is not None:
                match = next((x for x in agents if x.id == t.assigned_agent_id), None)
                if match:
                    task_dept[t.id] = match.department

        rows: list[CCWorkload] = []
        for dept, deps_agents in dept_agents.items():
            active_agents = sum(1 for a in deps_agents if a.is_active and a.status == "active")
            dept_tasks = [t for t in tasks_all if task_dept.get(t.id) == dept]
            active_tasks = sum(1 for t in dept_tasks if self._non_terminal(t.status))
            rows.append(CCWorkload(
                department=dept.upper(),
                agent_count=len(deps_agents),
                active_agents=active_agents,
                active_tasks=active_tasks,
                queued_tasks=sum(1 for t in dept_tasks if t.status in _QUEUED),
                verifying_tasks=sum(1 for t in dept_tasks if t.status in _VERIFYING),
                completed_tasks=sum(1 for t in dept_tasks if t.status in _COMPLETED),
                critical_tasks=sum(1 for t in dept_tasks if t.status not in _TERMINAL and t.priority == "P1"),
            ))
        return rows


    async def _activity_feed(
        self, tasks_all: list[Task], agent_by_id: dict[uuid.UUID, Agent],
    ) -> list[CCActivityEvent]:
        """Flatten the real per-task audit_log into a single newest-first feed."""
        events: list[CCActivityEvent] = []
        for t in tasks_all:
            for entry in t.audit_log or []:
                actor_raw = entry.get("actor")
                actor = actor_raw or "system"
                # Surface a real agent's name when the actor field is a raw UUID that
                # maps to an agent (never fabricated）。
                if isinstance(actor_raw, str) :
                    try:
                        if len(actor_raw) == 36:
                            _ = uuid.UUID(actor_raw)
                            uid = uuid.UUID(actor_raw)
                            if uid in agent_by_id:
                                actor = agent_by_id[uid].name
                    except ValueError:
                        pass
                events.append(CCActivityEvent(
                    task_id=t.id,
                    task_number=t.task_number,
                    timestamp=str(entry.get("timestamp") or ""),
                    action=entry.get("action") or "",
                    from_status=entry.get("from_status"),
                    to_status=entry.get("to_status"),
                    actor=actor,
                    message=entry.get("reason"),
                ))
        events.sort(key=lambda e: e.timestamp, reverse=True)
        return events[:25]

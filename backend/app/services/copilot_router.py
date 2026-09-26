"""
Copilot router — intent layer between the chat UI and the platform.

Decision flow for one free-text message (see Step 3 design):

  1. Deterministic keyword check FIRST, using the SAME `_DEPT_KEYWORDS`
     vocabulary the CommandCenterService command intake uses (imported,
     never re-declared) — the deterministic keyword match is authoritative
     whenever it fires.
  2. LLM intent classification via ollama_client.classify_intent() —
     assists/fills gaps only, never overrides a clean keyword match.
  3. Query path  → assemble a real-data context dict (CommandCenterOverview
     + whatever the text clearly mentions, via existing repos/services) and
     let ollama_client.summarize_context() explain it. NO Task is created.
  4. Command path → call CommandCenterService.create_command_task()
     UNCHANGED. NoAgentAvailable is surfaced as an honest failure message,
     never a fake success.

Hard rules honored here:
  * If classify_intent fell back (LLM down/unparseable), only the keyword
    result decides: keyword hit → command, no hit → query (safe default).
  * If the retrieved context is empty (tenant has no agents/tasks/etc.),
    the answer says so plainly — the LLM is only ever given real rows.
  * This module never commits; the route owns db.commit() (project
    convention).
"""
from __future__ import annotations

import logging
import time
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.alert_repository import AlertRepository
from app.repositories.collector_run_repository import CollectorRunRepository
from app.repositories.incident_repository import IncidentRepository
from app.repositories.sensor_repository import SensorRepository
from app.schemas.command_center import CommandIntake
from app.schemas.copilot import CopilotChatResponse
from app.services.ai_insight_service import AIInsightService
from app.services.command_center_service import (
    _DEPT_KEYWORDS,
    CommandCenterService,
)
from app.services.ollama_client import classify_intent, summarize_context
from app.services.sop_service import SOPService
from app.services.ticket_service import TicketService
from app.services.task_routing import NoAgentAvailable

logger = logging.getLogger(__name__)


def _keyword_department_hit(text: str) -> str | None:
    """Return the department whose keyword tuple the text matches, using
    the exact same vocabulary/order as CommandCenterService._pick_department.
    Returns None when NOTHING matched (i.e. the intake's noc default would
    be a guess, not a resolution)."""
    normalized = text.lower()
    for dept, keywords in _DEPT_KEYWORDS:
        if any(keyword in normalized for keyword in keywords):
            return dept
    return None


# Hard cap on how much of the Command Center aggregate is serialized into an
# LLM prompt. The full overview (up to 1000 agents + 1000 tasks with every
# field) is correct for the interactive dashboard, but feeding all of it to
# the local LLM blows through token/deadline budgets on a busy tenant.
_ASSISTANT_TASK_CONTEXT_LIMIT = 10
_ASSISTANT_AGENT_CONTEXT_LIMIT = 10
_ASSISTANT_ACTIVITY_CONTEXT_LIMIT = 20


def _compact_command_center(overview) -> dict:
    """A small, useful LLM-facing summary of a CommandCenterOverview.

    Turns the full dashboard aggregate into the columns the Copilot actually
    reasons over - headline metrics, the most recent/highest-priority tasks,
    active agents, per-department workload, and a few recent activity events.
    Never sends the full agent/task list to the AI.
    """
    dump = overview.model_dump(mode="json") if hasattr(overview, "model_dump") else overview
    tasks = dump.get("tasks") or []
    agents = dump.get("agents") or []
    activity = dump.get("activity") or []
    return {
        "generated_at": dump.get("generated_at"),
        "metrics": dump.get("metrics") or {},
        "workload": dump.get("workload") or [],
        "tasks": tasks[:_ASSISTANT_TASK_CONTEXT_LIMIT],
        "agents": [
            {
                "name": a.get("name"),
                "role": a.get("role"),
                "department": a.get("department"),
                "status": a.get("status"),
                "availability": a.get("availability"),
                "current_workload": a.get("current_workload"),
            }
            for a in agents[:_ASSISTANT_AGENT_CONTEXT_LIMIT]
        ],
        "activity": activity[:_ASSISTANT_ACTIVITY_CONTEXT_LIMIT],
    }


class CopilotRouter:
    """Handles one Copilot chat turn end-to-end. Built per request (same
    convention as every other service); owns no transaction."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.command_center = CommandCenterService(db)

    async def handle_message(
        self, tenant_id: uuid.UUID, message: str
    ) -> CopilotChatResponse:
        keyword_dept = _keyword_department_hit(message)

        # LLM assist. Never raises; fallback_used=True when unavailable.
        llm = await classify_intent(message)

        # --- Decision logic -------------------------------------------------
        if llm.fallback_used:
            # LLM unavailable: the deterministic keyword match alone decides.
            intent = "command" if keyword_dept else "query"
        elif llm.intent == "query":
            # A question must never create a Task, even if a department
            # keyword happened to appear in it.
            intent = "query"
        else:  # llm.intent == "command"
            intent = "command"

        if intent == "command":
            return await self._command_path(tenant_id, message, keyword_dept, llm)
        return await self._query_path(tenant_id, message, keyword_dept, llm)

    # ------------------------------------------------------------------ #
    # Command path                                                        #
    # ------------------------------------------------------------------ #

    async def _command_path(
        self,
        tenant_id: uuid.UUID,
        message: str,
        keyword_dept: str | None,
        llm,  # IntentResult
    ) -> CopilotChatResponse:
        context_used: dict = {
            "intent": "command",
            "keyword_department": keyword_dept,
            "llm_department_hint": llm.department_hint,
            "llm_needs_disambiguation": llm.needs_disambiguation,
            "llm_fallback_used": llm.fallback_used,
        }
        payload = CommandIntake(
            command=message,
            command_source="copilot",
        )
        try:
            task = await self.command_center.create_command_task(
                tenant_id, payload
            )
        except NoAgentAvailable as exc:
            dept_for_message = keyword_dept or llm.department_hint or "target"
            return CopilotChatResponse(
                answer=(
                    f"No available agent in the {dept_for_message} department "
                    f"to handle this request. No task was created. "
                    f"(Reason: {exc})"
                ),
                task_created=False,
                intent="command",
                context_used=context_used,
            )

        # Uncommitted; the route owns db.commit().
        await self.db.commit()

        # The task's department is derived from its assigned agent (see
        # CCTask.department). Report what ACTUALLY happened, including any
        # mismatch with the LLM hint - never hide the routing outcome.
        assigned_agent_name = None
        task_department = None
        if task.assigned_agent_id is not None:
            agent = await self.command_center.agent_repo.get(
                task.assigned_agent_id, tenant_id
            )
            if agent is not None:
                assigned_agent_name = agent.name
                task_department = agent.department
        if (
            llm.department_hint
            and task_department
            and llm.department_hint != task_department
            and keyword_dept is None
        ):
            context_used["routing_note"] = (
                f"LLM suggested '{llm.department_hint}' but deterministic "
                f"intake routed to '{task_department}'; the deterministic "
                f"result is authoritative."
            )

        return CopilotChatResponse(
            answer=(
                f"Created task {task.task_number} "
                f"\"{task.title}\""
                + (
                    f" and assigned it to {assigned_agent_name}"
                    f" ({task_department})."
                    if assigned_agent_name
                    else "."
                )
            ),
            task_created=True,
            task_id=task.id,
            task_number=task.task_number,
            department=task_department,
            agent=assigned_agent_name,
            intent="command",
            context_used=context_used,
        )

    # ------------------------------------------------------------------ #
    # Query path                                                          #
    # ------------------------------------------------------------------ #

    async def _query_path(
        self,
        tenant_id: uuid.UUID,
        message: str,
        keyword_dept: str | None,
        llm,  # IntentResult
    ) -> CopilotChatResponse:
        _t_start = time.perf_counter()
        context, notes = await self._build_query_context(tenant_id, message)
        _t_ctx = time.perf_counter()
        logger.info(
            "AI_REQUEST_START request=query EVENT_QUERY_DURATION=%.3f CONTEXT_BUILD_DURATION=%.3f",
            _t_ctx - _t_start,
            _t_ctx - _t_start,
        )
        context_used: dict = {
            "intent": "query",
            "keyword_department": keyword_dept,
            "llm_department_hint": llm.department_hint,
            "llm_fallback_used": llm.fallback_used,
            "data": context,
        }
        if notes:
            context_used["assembly_notes"] = notes

        # Short-circuit: nothing in the retrieved context at all -> answer
        # deterministically, do NOT bother the LLM with an empty prompt.
        if not any(context.values()):
            logger.info(
                "AI_REQUEST_START request=query OLLAMA_DURATION=0.0 AI_REQUEST_TOTAL=%.3f",
                time.perf_counter() - _t_start,
            )
            return CopilotChatResponse(
                answer=(
                    "No data is available yet for this workspace - no agents, "
                    "tasks, alerts, incidents, or tickets have been recorded. "
                    "Once monitoring data starts flowing I can summarize it here."
                ),
                task_created=False,
                intent="query",
                context_used=context_used,
            )

        answer = await summarize_context(context)
        _t_ollama = time.perf_counter()
        logger.info(
            "AI_REQUEST_START request=query OLLAMA_DURATION=%.3f AI_REQUEST_TOTAL=%.3f",
            _t_ollama - _t_ctx,
            _t_ollama - _t_start,
        )
        return CopilotChatResponse(
            answer=answer,
            task_created=False,
            intent="query",
            context_used=context_used,
        )

    async def _build_query_context(
        self, tenant_id: uuid.UUID, message: str
    ) -> tuple[dict, list[str]]:
        """Assemble the query context from EXISTING repos/services only.

        CommandCenterOverview is always included (agents/tasks/workload/
        activity). Everything else is included only when the text clearly
        mentions it, to keep the LLM prompt small and focused. All
        failures are logged and degrade to omission, never raised."""
        context: dict = {}
        notes: list[str] = []
        normalized = message.lower()

        # Always: the Command Center aggregate (real Agent/Task rows), sent to
        # the LLM as a bounded compact summary - never the full up-to-1000-row
        # dashboard dump, which would balloon context size and blow the AI
        # request deadline on a busy tenant.
        try:
            overview = await self.command_center.overview(tenant_id)
            context["command_center"] = _compact_command_center(overview)
        except Exception as exc:  # noqa: BLE001 - degrade, don't fail the turn
            logger.warning("Copilot query: overview unavailable: %s", exc)
            notes.append("command_center overview unavailable")

        mentions = {
            "alerts": ("alert", "critical", "warning", "down", "flapping"),
            "insights": ("insight", "anomaly", "collector", "no data", "sustained"),
            "incidents": ("incident", "outage", "correlation"),
            "tickets": ("ticket", "request", "itsm"),
            "sops": ("sop", "runbook", "procedure"),
        }

        if any(k in normalized for k in mentions["alerts"] + mentions["insights"]):
            try:
                insights = await AIInsightService(
                    alert_repo=AlertRepository(self.db),
                    sensor_repo=SensorRepository(self.db),
                    run_repo=CollectorRunRepository(self.db),
                ).insights(tenant_id)
                context["ai_insights"] = insights.model_dump(mode="json")
            except Exception as exc:  # noqa: BLE001
                logger.warning("Copilot query: insights unavailable: %s", exc)
                notes.append("ai insights unavailable")

        if any(k in normalized for k in mentions["incidents"]):
            try:
                incidents = await IncidentRepository(self.db).list(
                    tenant_id, limit=10
                )
                context["recent_incidents"] = [
                    {
                        "incident_number": i.incident_number,
                        "title": i.title,
                        "status": i.status,
                        "severity": str(getattr(i, "severity", "") or ""),
                    }
                    for i in incidents
                ]
            except Exception as exc:  # noqa: BLE001
                logger.warning("Copilot query: incidents unavailable: %s", exc)
                notes.append("incidents unavailable")

        if any(k in normalized for k in mentions["tickets"]):
            try:
                tickets, _total = await TicketService(self.db).list_tickets(
                    tenant_id=tenant_id, limit=10
                )
                context["recent_tickets"] = [
                    {
                        "ticket_number": getattr(t, "ticket_number", None),
                        "title": t.title,
                        "status": t.status,
                        "priority": str(getattr(t, "priority", "") or ""),
                    }
                    for t in tickets
                ]
            except Exception as exc:  # noqa: BLE001
                logger.warning("Copilot query: tickets unavailable: %s", exc)
                notes.append("tickets unavailable")

        if any(k in normalized for k in mentions["sops"]):
            try:
                sops = await SOPService(self.db).list_sops(tenant_id, limit=10)
                context["sops"] = [
                    {"title": s.title, "category": getattr(s, "category", None)}
                    for s in sops
                ]
            except Exception as exc:  # noqa: BLE001
                logger.warning("Copilot query: sops unavailable: %s", exc)
                notes.append("sops unavailable")

        return context, notes

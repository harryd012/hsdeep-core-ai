"""
Incident Lifecycle Service (Sprint 15 + Phase 1 Lifecycle State Machine).

Owns the extended enterprise incident lifecycle:
    open -> acknowledged -> investigating -> monitoring -> recovered
    -> closed -> archived

Each transition is validated against the allowed forward path, records a
timestamp on the Incident, and writes an AlertHistory-style note. The
service also runs `auto_close_stale()` which transitions incidents that
have been inactive beyond their configurable `stale_after_minutes`
threshold from `monitoring`/`open` -> `closed`, satisfying the sprint's
"Automatically close stale incidents after configurable inactivity"
requirement.

Phase 1 (Lifecycle State Machine Extension): the lifecycle is widened to a
superset that no longer parallels the existing states but represents the
same points in the flow:

  open -> correlated -> prioritized -> acknowledged -> investigating
       -> rca_ready -> remediation_pending -> remediating -> validating
       -> recovered -> closed -> archived

plus exception states that branch off mid-flow and merge back in:
`escalated`, `waiting_vendor`, `waiting_customer`, `waiting_change`,
`failed_remediation`, and `reopened`. These never create a parallel
concept - they are reachable from the relevant mid-flow states and
transition back into the main flow (e.g. `failed_remediation` ->
`remediating` for retry, `reopened` -> `investigating`).

Design notes:
- The original 3-state lifecycle (open|acknowledged|resolved) is preserved
  for backward compatibility. `resolved` is treated as equivalent to
  `recovered` by this service, so legacy code that calls
  IncidentRepository.resolve() still works through the validated path.
- This service does NOT own incident creation/grouping (that's
  CorrelationEngine's job). It only owns state transitions on already-
  existing incidents, same "one service, one responsibility" split as the
  rest of this codebase.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models.incident import Incident
from app.repositories.incident_repository import IncidentRepository


# Ordered lifecycle states. `resolved` is included for backward
# compatibility with the original 3-state model and is treated as a synonym
# for `recovered` by _normalize_status(). The exception states are listed
# after the main-flow states for readability but are all valid statuses.
LIFECYCLE_STATES: tuple[str, ...] = (
    "open",
    "correlated",
    "prioritized",
    "acknowledged",
    "investigating",
    "rca_ready",
    "remediation_pending",
    "remediating",
    "validating",
    "monitoring",
    "recovered",
    "closed",
    "archived",
    # exception states (branch off from / merge back into the main flow)
    "escalated",
    "waiting_vendor",
    "waiting_customer",
    "waiting_change",
    "failed_remediation",
    "reopened",
    # legacy synonym for recovered
    "resolved",
)

# Allowed transitions. An incident can always move forward; it can also
# move backward from monitoring -> investigating (operator re-opens
# investigation) but never from closed/archived back to an active state
# (only `reopened` re-enters the flow from a recovered incident).
#
# Phase 1 adds the pre-investigation states (correlated, prioritized), the
# RCA/remediation pipeline (rca_ready, remediation_pending, remediating,
# validating), and the exception states (escalated, waiting_vendor,
# waiting_customer, waiting_change, failed_remediation, reopened). These
# map onto the *same* flow points as the Sprint 15 states - they are not
# parallel concepts.
_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    # -- main flow --------------------------------------------------------
    "open": {
        "correlated",
        "prioritized",
        "acknowledged",
        "investigating",
        "recovered",
        "closed",
        "escalated",
    },
    "correlated": {
        "prioritized",
        "acknowledged",
        "investigating",
        "recovered",
        "closed",
        "escalated",
    },
    "prioritized": {
        "acknowledged",
        "investigating",
        "recovered",
        "closed",
        "escalated",
    },
    "acknowledged": {
        "investigating",
        "monitoring",
        "recovered",
        "closed",
        "escalated",
    },
    "investigating": {
        "rca_ready",
        "monitoring",
        "recovered",
        "closed",
        "escalated",
        "waiting_vendor",
        "waiting_customer",
        "waiting_change",
    },
    "rca_ready": {
        "remediation_pending",
        "remediating",  # skip straight to remediating if no pending step
        "monitoring",
        "recovered",
        "closed",
        "escalated",
        "waiting_vendor",
        "waiting_customer",
        "waiting_change",
    },
    "remediation_pending": {
        "remediating",
        "monitoring",
        "recovered",
        "closed",
        "escalated",
        "waiting_vendor",
        "waiting_customer",
        "waiting_change",
    },
    "remediating": {
        "validating",
        "monitoring",
        "recovered",
        "closed",
        "failed_remediation",
        "escalated",
        "waiting_vendor",
        "waiting_customer",
        "waiting_change",
    },
    "validating": {
        "recovered",
        "remediating",  # validation failed -> retry remediation
        "monitoring",
        "closed",
        "failed_remediation",
        "escalated",
        "waiting_vendor",
        "waiting_customer",
        "waiting_change",
    },
    "monitoring": {
        "investigating",  # operator re-opens investigation
        "rca_ready",
        "recovered",
        "closed",
        "escalated",
    },
    "recovered": {"closed", "archived", "reopened"},
    # `resolved` (legacy) is NOT a dict key: _normalize_status() maps a
    # current `resolved` -> `recovered`, so legacy rows route through the
    # `recovered` rules. `resolved` is therefore never a valid *target* -
    # the single canonical forward-write value is `recovered`.
    "closed": {"archived"},
    "archived": set(),

    # -- exception states --------------------------------------------------
    # All of these can merge back into the main flow (they never dead-end)
    # except when the operator decides to close the incident outright.
    #
    # Note on `closed -> reopened`: deliberately EXCLUDED. `closed` is a
    # terminal, auditable state - the only onward transition is `archived`.
    # Allowing a closed incident to re-enter the flow would make `closed`
    # ambiguous and break the "closed is a done-ledger invariant" contract.
    # If an already-closed incident genuinely needs work again, the caller
    # should open a NEW incident (fresh trail) rather than mutate the closed
    # ledger entry. `reopened` exists only to pull an incident that was
    # `recovered` (e.g. a monitoring false-positive off a recovery) back into
    # the investigation flow.
    "escalated": {
        "investigating",
        "rca_ready",
        "remediating",
        "monitoring",
        "recovered",
        "acknowledged",
        "closed",
        "waiting_vendor",
        "waiting_customer",
        "waiting_change",
    },
    "waiting_vendor": {
        "remediating",
        "investigating",
        "escalated",
        "monitoring",
        "recovered",
        "closed",
    },
    "waiting_customer": {
        "remediating",
        "investigating",
        "escalated",
        "monitoring",
        "recovered",
        "closed",
    },
    "waiting_change": {
        "remediating",
        "validating",
        "investigating",
        "escalated",
        "monitoring",
        "recovered",
        "closed",
    },
    "failed_remediation": {
        "remediating",  # retry remediation
        "escalated",  # escalate to a human / higher tier
        "investigating",
        "monitoring",
        "recovered",
        "closed",
        "waiting_vendor",
        "waiting_customer",
        "waiting_change",
    },
    "reopened": {
        "investigating",
        "escalated",
        "closed",
    },
}

# Timestamp field to set for each transition state. Phase 1 does not add
# per-state timestamp columns for the new states (that's Phase 2
# instrumentation); the Sprint 15 columns remain the ones we touch.
_TRANSITION_TIMESTAMP_FIELD: dict[str, str] = {
    "acknowledged": "acknowledged_at",
    "investigating": "investigating_at",
    "monitoring": "monitoring_at",
    "recovered": "recovered_at",
    "resolved": "resolved_at",
    "closed": "closed_at",
    "archived": "archived_at",
}

# Auto-close allow-list: only these states represent an incident that is
# stale *because nothing is happening*. Remediation states
# (remediation_pending/validating), which indicate active in-flight work,
# are deliberately NOT auto-closed - closing them out from under a running
# remediation would corrupt the data flow.
AUTO_CLOSE_SOURCE_STATES: tuple[str, ...] = (
    "open",
    "acknowledged",
    "investigating",
    "monitoring",
)


class IncidentLifecycleError(Exception):
    """Raised when a transition is not allowed by the lifecycle rules."""


class IncidentLifecycleService:
    def __init__(self, incident_repo: IncidentRepository):
        self.incident_repo = incident_repo

    # -- operator-driven transitions -----------------------------------------

    async def acknowledge(
        self, incident_id: uuid.UUID, tenant_id: uuid.UUID, *, acknowledged_by: uuid.UUID
    ) -> Incident:
        incident = await self._get_or_raise(incident_id, tenant_id)
        self._validate_transition(incident.status, "acknowledged")
        incident.status = "acknowledged"
        incident.acknowledged_at = datetime.now(timezone.utc)
        incident.acknowledged_by = acknowledged_by
        await self.incident_repo.db.flush()
        await self.incident_repo.db.refresh(incident)
        return incident

    async def start_investigating(self, incident_id: uuid.UUID, tenant_id: uuid.UUID) -> Incident:
        incident = await self._get_or_raise(incident_id, tenant_id)
        self._validate_transition(incident.status, "investigating")
        incident.status = "investigating"
        incident.investigating_at = datetime.now(timezone.utc)
        await self.incident_repo.db.flush()
        await self.incident_repo.db.refresh(incident)
        return incident

    async def start_monitoring(self, incident_id: uuid.UUID, tenant_id: uuid.UUID) -> Incident:
        incident = await self._get_or_raise(incident_id, tenant_id)
        self._validate_transition(incident.status, "monitoring")
        incident.status = "monitoring"
        incident.monitoring_at = datetime.now(timezone.utc)
        await self.incident_repo.db.flush()
        await self.incident_repo.db.refresh(incident)
        return incident

    async def mark_recovered(self, incident_id: uuid.UUID, tenant_id: uuid.UUID) -> Incident:
        incident = await self._get_or_raise(incident_id, tenant_id)
        self._validate_transition(incident.status, "recovered")
        now = datetime.now(timezone.utc)
        incident.status = "recovered"
        incident.recovered_at = now
        incident.resolved_at = now  # backward compat
        await self.incident_repo.db.flush()
        await self.incident_repo.db.refresh(incident)
        return incident

    async def close(self, incident_id: uuid.UUID, tenant_id: uuid.UUID) -> Incident:
        incident = await self._get_or_raise(incident_id, tenant_id)
        self._validate_transition(incident.status, "closed")
        incident.status = "closed"
        incident.closed_at = datetime.now(timezone.utc)
        await self.incident_repo.db.flush()
        await self.incident_repo.db.refresh(incident)
        return incident

    async def archive(self, incident_id: uuid.UUID, tenant_id: uuid.UUID) -> Incident:
        incident = await self._get_or_raise(incident_id, tenant_id)
        self._validate_transition(incident.status, "archived")
        incident.status = "archived"
        incident.archived_at = datetime.now(timezone.utc)
        await self.incident_repo.db.flush()
        await self.incident_repo.db.refresh(incident)
        return incident

    async def assign(
        self,
        incident_id: uuid.UUID,
        tenant_id: uuid.UUID,
        *,
        assigned_to: uuid.UUID | None = None,
        assigned_team: str | None = None,
    ) -> Incident:
        """Assigns an incident to a user and/or team. Does not change the
        lifecycle status - assignment is orthogonal to state."""
        incident = await self._get_or_raise(incident_id, tenant_id)
        if assigned_to is not None:
            incident.assigned_to = assigned_to
        if assigned_team is not None:
            incident.assigned_team = assigned_team
        await self.incident_repo.db.flush()
        await self.incident_repo.db.refresh(incident)
        return incident

    # -- automatic stale-incident closure -----------------------------------

    async def auto_close_stale(self, tenant_id: uuid.UUID) -> list[Incident]:
        """Finds all incidents in an auto-close-eligible state
        (open/acknowledged/investigating/monitoring - see
        AUTO_CLOSE_SOURCE_STATES) whose `last_alert_at` is older than
        their `stale_after_minutes` threshold and transitions them to
        `closed`. Returns the list of closed incidents so the caller
        (scheduler) can log how many were auto-closed.

        Phase 1 hardening: auto-close is restricted to those four source
        states and every write goes through `_validate_transition`. We do
        NOT auto-close `remediating`, `validating`, or
        `remediation_pending` - those represent active in-flight work, not
        staleness, and closing them out from under a live remediation
        would be a data-integrity problem.

        This is the sprint's "Automatically close stale incidents after
        configurable inactivity" requirement - run periodically by the
        scheduler (see app/scheduler/scheduler.py).
        """
        now = datetime.now(timezone.utc)

        # Fetch all incidents in the eligible source states for this tenant.
        # We filter in Python rather than SQL because `stale_after_minutes`
        # is per-incident (configurable), so the cutoff differs per row.
        stmt = (
            select(Incident)
            .where(
                Incident.tenant_id == tenant_id,
                Incident.status.in_(AUTO_CLOSE_SOURCE_STATES),
                Incident.is_deleted.is_(False),
            )
            .order_by(Incident.last_alert_at.desc())
        )
        result = await self.incident_repo.db.execute(stmt)
        active_incidents = list(result.scalars().all())

        closed: list[Incident] = []
        for incident in active_incidents:
            cutoff = incident.last_alert_at + timedelta(minutes=incident.stale_after_minutes)
            if now > cutoff:
                # Explicit allow-list guard, then validated transition -
                # never a blanket bypass. The validator re-checks the
                # source state against _ALLOWED_TRANSITIONS, so a stale
                # incident in a non-allow-listed state can never be
                # auto-closed here.
                if incident.status not in AUTO_CLOSE_SOURCE_STATES:
                    continue
                self._validate_transition(incident.status, "closed")
                incident.status = "closed"
                incident.closed_at = now
                closed.append(incident)

        if closed:
            await self.incident_repo.db.flush()
            for incident in closed:
                await self.incident_repo.db.refresh(incident)

        return closed

    # -- internals -----------------------------------------------------------

    async def _get_or_raise(self, incident_id: uuid.UUID, tenant_id: uuid.UUID) -> Incident:
        incident = await self.incident_repo.get(incident_id, tenant_id)
        if incident is None:
            raise IncidentLifecycleError(f"Incident {incident_id} not found")
        return incident

    @staticmethod
    def _normalize_status(status: str) -> str:
        """Treats `resolved` (legacy) as `recovered` for transition-rule
        purposes, so the original 3-state lifecycle still works."""
        return "recovered" if status == "resolved" else status

    @classmethod
    def _validate_transition(cls, current: str, target: str) -> None:
        if target == "resolved":
            # Option A (Phase 1): `resolved` is a legacy READ-ONLY status.
            # The canonical forward-write value is `recovered`; `resolved`
            # is never a valid write target. Fail fast with a clear message
            # rather than a confusing "not allowed" so nobody reintroduces
            # the unreachable `resolved` transition.
            raise IncidentLifecycleError(
                "'resolved' is a legacy read-only status - use 'recovered' "
                f"instead (from {current!r})"
            )
        normalized_current = cls._normalize_status(current)
        allowed = _ALLOWED_TRANSITIONS.get(normalized_current, set())
        if target not in allowed:
            raise IncidentLifecycleError(
                f"Lifecycle transition not allowed: {current!r} -> {target!r}"
            )

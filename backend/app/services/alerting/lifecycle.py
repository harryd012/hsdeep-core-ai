"""Incident lifecycle: resolution-on-clear and stale auto-close.

Two deterministic, DB-only rules (same "honest heuristic" philosophy as the
correlation engine — no ML, no scores):

  * ``resolve_on_clear``: when an alert in an incident clears (auto-resolves),
    the incident is resolved too once it has NO remaining active member alerts.
  * ``close_stale_incidents``: an open/acknowledged/… incident that receives no
    new alert for ``incident_stale_minutes`` is auto-closed with a recorded
    ``closure_reason``.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from app.models.incident import Incident
from app.repositories.alert_repository import AlertRepository
from app.repositories.incident_repository import IncidentRepository

logger = logging.getLogger(__name__)

# Statuses considered "still open" for the stale-close sweep. "resolved" and
# "closed"/"archived" are terminal; "recovered" is a deliberate hold state and
# is intentionally NOT auto-closed here.
_STALE_ELIGIBLE_STATUSES = ("open", "acknowledged", "investigating", "monitoring")


async def resolve_on_clear(
    alert_repo: AlertRepository,
    incident_repo: IncidentRepository,
    incident: Incident | None,
) -> Incident | None:
    """Resolve ``incident`` if no active (non-resolved) member alert remains.

    Returns the resolved incident, or ``None`` if it was already terminal or
    still has active members (nothing to do). Safe to call after any single
    alert clears.
    """
    if incident is None or incident.status in ("resolved", "closed", "archived"):
        return None
    remaining = await alert_repo.count_active_for_incident(incident.id)
    if remaining > 0:
        return None
    return await incident_repo.resolve(incident)


async def close_stale_incidents(
    incident_repo: IncidentRepository,
    *,
    stale_minutes: int,
    now: datetime | None = None,
    limit: int = 500,
) -> int:
    """Auto-close incidents that have received no new alert for ``stale_minutes``.

    Returns the number of incidents closed. Each closure records the reason in
    ``closure_reason`` so operators can tell an auto-close from a manual one.
    """
    now = now or datetime.now(timezone.utc)
    before = now - timedelta(minutes=stale_minutes)
    stale = await incident_repo.list_stale_open(before, limit=limit)
    if not stale:
        return 0

    reason = f"Auto-closed: no new alert for {stale_minutes} minutes."
    closed = 0
    for incident in stale:
        await incident_repo.close_stale(incident, reason=reason)
        closed += 1
    logger.info("Stale-close sweep: closed %s stale incident(s).", closed)
    return closed

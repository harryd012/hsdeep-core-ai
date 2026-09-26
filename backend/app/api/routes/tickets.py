"""
Enterprise Ticket Management Engine - API Routes.

Provides endpoints for:
- Ticket CRUD operations
- Ticket lifecycle management (assign, resolve, close, reopen)
- Comments and attachments
- ITSM provider linking
- Dashboard statistics
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import Principal, get_current_user, get_tenant_id, require_permission
from app.core.db import get_db
from app.schemas.assignment import SmartAssignRequest
from app.schemas.ticket import (
    TicketOut,
    TicketCreate,
    TicketUpdate,
    TicketAssign,
    TicketCommentCreate,
    TicketCommentOut,
    TicketAttachmentOut,
    TicketLinkOut,
    TicketHistoryOut,
    TicketListResponse,
    TicketDashboardStats,
    TicketTransition,
    TicketTransitionResponse,
)
from app.core.entitlements import require_active_subscription
from app.services.assignment_service import AssignmentError, AssignmentService
from app.services.ticket_service import TicketService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tickets", tags=["tickets"])

# Write-route guard: suspended/canceled/incomplete subscriptions get a 402
# ("subscription_suspended"). GET routes are intentionally NOT gated so a
# suspended tenant can still view its data and manage billing. past_due
# passes (grace period is intentional).
_active_subscription_guard = Depends(require_active_subscription)


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------

async def get_ticket_service(
    db: AsyncSession = Depends(get_db),
) -> TicketService:
    """Provide a TicketService instance for each request."""
    return TicketService(db)


# ---------------------------------------------------------------------------
# Ticket CRUD
# ---------------------------------------------------------------------------

@router.get("/", response_model=TicketListResponse)
async def list_tickets(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    service: TicketService = Depends(get_ticket_service),
    status: str | None = None,
    priority: str | None = None,
    assigned_to: uuid.UUID | None = None,
    category: str | None = None,
    incident_id: uuid.UUID | None = None,
    limit: int = Query(50, le=100),
    offset: int = Query(0, ge=0),
    search: str | None = None,
):
    """List tickets with filters and pagination."""
    tickets, total = await service.list_tickets(
        tenant_id=tenant_id,
        status=status,
        priority=priority,
        assigned_to=assigned_to,
        category=category,
        incident_id=incident_id,
        limit=limit,
        offset=offset,
        search=search,
    )
    return TicketListResponse(
        items=[TicketOut.model_validate(t) for t in tickets],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/dashboard", response_model=TicketDashboardStats)
async def get_dashboard_stats(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    service: TicketService = Depends(get_ticket_service),
):
    """Get ticket dashboard statistics."""
    from app.repositories.ticket_repository import TicketRepository
    repo = TicketRepository(service.db)

    open_tickets = await repo.list(tenant_id, status="open")
    assigned_tickets = await repo.list(tenant_id, status="assigned")
    in_progress = await repo.list(tenant_id, status="in_progress")
    pending_approval = await repo.list(tenant_id, status="pending_approval")
    resolved_today = await repo.list(tenant_id, status="resolved")
    closed_today = await repo.list(tenant_id, status="closed")
    escalated = await repo.get_escalated(tenant_id)
    overdue = await repo.get_overdue(tenant_id)
    sla_breached = await repo.get_sla_breached(tenant_id)
    priority_dist = await repo.priority_distribution(tenant_id)

    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    def filter_today(tickets: list[Any]) -> int:
        return sum(1 for t in tickets if t.resolved_at and t.resolved_at >= today_start)

    return TicketDashboardStats(
        open_count=len(open_tickets),
        assigned_count=len(assigned_tickets),
        in_progress_count=len(in_progress),
        pending_approval_count=len(pending_approval),
        resolved_today_count=filter_today(resolved_today),
        closed_today_count=filter_today(closed_today),
        escalated_count=len(escalated),
        overdue_count=len(overdue),
        sla_breach_count=len(sla_breached),
        priority_distribution=priority_dist,
    )


@router.get("/{ticket_id}", response_model=TicketOut)
async def get_ticket(
    ticket_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    service: TicketService = Depends(get_ticket_service),
):
    """Get a ticket by ID."""
    ticket = await service.get_ticket(ticket_id, tenant_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return TicketOut.model_validate(ticket)


@router.post("/", response_model=TicketOut, status_code=201,
             dependencies=[_active_subscription_guard])
async def create_ticket(
    payload: TicketCreate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    _: Principal = Depends(require_permission("tickets.write")),
    service: TicketService = Depends(get_ticket_service),
):
    """Create a new ticket."""
    try:
        ticket = await service.create_ticket(
            tenant_id=tenant_id,
            title=payload.title,
            description=payload.description,
            incident_id=payload.incident_id,
            source=payload.source,
            priority=payload.priority,
            category=payload.category,
            subcategory=payload.subcategory,
            assigned_to=payload.assigned_to,
            assigned_team=payload.assigned_team,
            assignment_group=payload.assignment_group,
            sla_due_at=payload.sla_due_at,
            custom_fields=payload.custom_fields,
            tags=payload.tags,
            created_by=payload.created_by,
        )
        await db.commit()
        await db.refresh(ticket)
        return TicketOut.model_validate(ticket)
    except Exception as exc:
        logger.exception("Failed to create ticket")
        raise HTTPException(status_code=500, detail=str(exc))


@router.patch("/{ticket_id}", response_model=TicketOut,
              dependencies=[_active_subscription_guard])
async def update_ticket(
    ticket_id: uuid.UUID,
    payload: TicketUpdate,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    _: Principal = Depends(require_permission("tickets.write")),
    service: TicketService = Depends(get_ticket_service),
):
    """Update a ticket."""
    ticket = await service.get_ticket(ticket_id, tenant_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    updates = payload.model_dump(exclude_unset=True, exclude={"updated_by"})
    updated = await service.update_ticket(ticket, **updates)
    return TicketOut.model_validate(updated)


@router.delete("/{ticket_id}", status_code=204,
               dependencies=[_active_subscription_guard])
async def delete_ticket(
    ticket_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    _: Principal = Depends(require_permission("tickets.write")),
    service: TicketService = Depends(get_ticket_service),
):
    """Soft-delete a ticket."""
    ticket = await service.get_ticket(ticket_id, tenant_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    await service.tickets.soft_delete(ticket)


# ---------------------------------------------------------------------------
# Ticket Lifecycle
# ---------------------------------------------------------------------------

@router.post("/{ticket_id}/assign", response_model=TicketOut,
             dependencies=[_active_subscription_guard])
async def assign_ticket(
    ticket_id: uuid.UUID,
    payload: TicketAssign,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    _: Principal = Depends(require_permission("tickets.execute")),
    service: TicketService = Depends(get_ticket_service),
):
    """Assign a ticket to a user."""
    ticket = await service.get_ticket(ticket_id, tenant_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    updated = await service.assign_ticket(
        ticket,
        assigned_to=payload.assigned_to,
        assigned_by=payload.assigned_by,
    )
    return TicketOut.model_validate(updated)


@router.post("/{ticket_id}/assign-smart", response_model=TicketOut,
             dependencies=[_active_subscription_guard])
async def assign_smart(
    ticket_id: uuid.UUID,
    payload: SmartAssignRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    _: Principal = Depends(require_permission("tickets.execute")),
    service: TicketService = Depends(get_ticket_service),
):
    """Assign a ticket using the smart assignment engine (strategy-based)."""
    ticket = await service.get_ticket(ticket_id, tenant_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    try:
        assignment_service = AssignmentService(db)
        updated = await assignment_service.assign_smart(
            ticket,
            tenant_id=tenant_id,
            group_id=payload.group_id,
            strategy=payload.strategy,
            assigned_by=payload.assigned_by,
            reason=payload.reason,
        )
        await db.commit()
        await db.refresh(ticket)
        return TicketOut.model_validate(ticket)
    except AssignmentError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/{ticket_id}/resolve", response_model=TicketOut,
             dependencies=[_active_subscription_guard])
async def resolve_ticket(
    ticket_id: uuid.UUID,
    resolution_note: str | None = None,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    _: Principal = Depends(require_permission("tickets.write")),
    service: TicketService = Depends(get_ticket_service),
):
    """Resolve a ticket."""
    ticket = await service.get_ticket(ticket_id, tenant_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    updated = await service.resolve_ticket(ticket, resolution_note=resolution_note)
    return TicketOut.model_validate(updated)


@router.post("/{ticket_id}/close", response_model=TicketOut,
             dependencies=[_active_subscription_guard])
async def close_ticket(
    ticket_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    _: Principal = Depends(require_permission("tickets.write")),
    service: TicketService = Depends(get_ticket_service),
):
    """Close a resolved ticket."""
    ticket = await service.get_ticket(ticket_id, tenant_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    updated = await service.close_ticket(ticket)
    return TicketOut.model_validate(updated)


@router.post("/{ticket_id}/reopen", response_model=TicketOut,
             dependencies=[_active_subscription_guard])
async def reopen_ticket(
    ticket_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    _: Principal = Depends(require_permission("tickets.write")),
    service: TicketService = Depends(get_ticket_service),
):
    """Reopen a closed ticket."""
    ticket = await service.get_ticket(ticket_id, tenant_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    updated = await service.reopen_ticket(ticket)
    return TicketOut.model_validate(updated)


@router.post("/{ticket_id}/transition", response_model=TicketTransitionResponse,
             dependencies=[_active_subscription_guard])
async def transition_ticket(
    ticket_id: uuid.UUID,
    payload: TicketTransition,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    _: Principal = Depends(require_permission("tickets.write")),
    principal: Principal = Depends(get_current_user),
    service: TicketService = Depends(get_ticket_service),
):
    """Transition a ticket to a new state using the state machine.
    
    This is the ONLY way to change ticket status. Direct status updates
    are not allowed.
    """
    ticket = await service.get_ticket(ticket_id, tenant_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    try:
        updated = await service.transition_ticket(
            ticket,
            to_state=payload.to_state,
            actor_id=principal.user_id,
            actor_name=principal.email,
            reason=payload.reason,
            context=payload.context,
        )
        
        # Get available transitions for the new state
        from app.services.ticket_state_machine import TicketStateMachine
        state_machine = TicketStateMachine(service.db, updated)
        available = state_machine.get_available_transitions()
        
        return TicketTransitionResponse(
            ticket=TicketOut.model_validate(updated),
            available_transitions=available,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ---------------------------------------------------------------------------
# Comments
# ---------------------------------------------------------------------------

@router.get("/{ticket_id}/comments", response_model=list[TicketCommentOut])
async def get_comments(
    ticket_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    service: TicketService = Depends(get_ticket_service),
):
    """Get all comments for a ticket."""
    ticket = await service.get_ticket(ticket_id, tenant_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    comments = await service.tickets.get_comments(ticket_id)
    return [TicketCommentOut.model_validate(c) for c in comments]


@router.post("/{ticket_id}/comments", response_model=TicketCommentOut, status_code=201,
             dependencies=[_active_subscription_guard])
async def add_comment(
    ticket_id: uuid.UUID,
    payload: TicketCommentCreate,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    _: Principal = Depends(require_permission("tickets.write")),
    service: TicketService = Depends(get_ticket_service),
):
    """Add a comment to a ticket."""
    ticket = await service.get_ticket(ticket_id, tenant_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    comment = await service.add_comment(
        ticket,
        body=payload.body,
        author_id=payload.author_id,
        author_name=payload.author_name,
        is_internal=payload.is_internal,
    )
    return TicketCommentOut.model_validate(comment)


# ---------------------------------------------------------------------------
# Attachments
# ---------------------------------------------------------------------------

@router.get("/{ticket_id}/attachments", response_model=list[TicketAttachmentOut])
async def get_attachments(
    ticket_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    service: TicketService = Depends(get_ticket_service),
):
    """Get all attachments for a ticket."""
    ticket = await service.get_ticket(ticket_id, tenant_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    attachments = await service.tickets.get_attachments(ticket_id)
    return [TicketAttachmentOut.model_validate(a) for a in attachments]


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------

@router.get("/{ticket_id}/history", response_model=list[TicketHistoryOut])
async def get_history(
    ticket_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    service: TicketService = Depends(get_ticket_service),
):
    """Get all history entries for a ticket."""
    ticket = await service.get_ticket(ticket_id, tenant_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    history = await service.tickets.get_history(ticket_id)
    return [TicketHistoryOut.model_validate(h) for h in history]


# ---------------------------------------------------------------------------
# ITSM Links
# ---------------------------------------------------------------------------

@router.get("/{ticket_id}/links", response_model=list[TicketLinkOut])
async def get_links(
    ticket_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    service: TicketService = Depends(get_ticket_service),
):
    """Get all ITSM links for a ticket."""
    ticket = await service.get_ticket(ticket_id, tenant_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    links = await service.tickets.get_links(ticket_id)
    return [TicketLinkOut.model_validate(l) for l in links]
"""
Saved dashboards (docs/design/OBSERVABILITY_PLATFORM_PLAN.md, P6 slice 2).

Same ownership rules as saved host views: a user lists their own dashboards
and those shared in the tenant, and changes only their own (another user's or
tenant's dashboard is 'not found'). A widget that names a sensor or interface
must name one of the caller's tenant, so a dashboard never stores another
tenant's ids. Create / update / delete are audit events (`dashboard.*`).
"""
from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.interface import Interface
from app.models.saved_dashboard import MAX_DASHBOARDS_PER_OWNER, SavedDashboard
from app.models.sensor import Sensor
from app.schemas.dashboards import DashboardCreate, DashboardOut, DashboardSummary, DashboardUpdate, Widget
from app.services.audit_service import AuditService
from app.services.host_inventory_service import Actor


class DashboardNotFound(Exception):
    pass


class DashboardConflict(Exception):
    """Name already used by this owner, or the owner's dashboard limit is reached."""


class WidgetTargetInvalid(ValueError):
    pass


def dashboard_out(d: SavedDashboard, actor: Actor) -> DashboardOut:
    return DashboardOut(
        id=d.id, name=d.name, description=d.description,
        widgets=[Widget.model_validate(w) for w in d.widgets or []],
        shared=d.shared, owned=d.owner_id == actor.user_id, created_at=d.created_at, updated_at=d.updated_at,
    )


def dashboard_summary(d: SavedDashboard, actor: Actor) -> DashboardSummary:
    return DashboardSummary(
        id=d.id, name=d.name, description=d.description, widget_count=len(d.widgets or []),
        shared=d.shared, owned=d.owner_id == actor.user_id, updated_at=d.updated_at,
    )


class DashboardStore:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.audit = AuditService(db)

    async def list(self, actor: Actor) -> list[SavedDashboard]:
        """The caller's own dashboards, then those others shared in the tenant; by name."""
        stmt = (
            select(SavedDashboard)
            .where(
                SavedDashboard.tenant_id == actor.tenant_id,
                (SavedDashboard.owner_id == actor.user_id) | SavedDashboard.shared.is_(True),
            )
            .order_by((SavedDashboard.owner_id != actor.user_id).asc(), func.lower(SavedDashboard.name), SavedDashboard.id)
        )
        return list((await self.db.execute(stmt)).scalars())

    async def get(self, actor: Actor, dashboard_id: uuid.UUID) -> SavedDashboard:
        """One the caller owns or that is shared in the tenant."""
        d = (
            await self.db.execute(
                select(SavedDashboard).where(
                    SavedDashboard.id == dashboard_id,
                    SavedDashboard.tenant_id == actor.tenant_id,
                    (SavedDashboard.owner_id == actor.user_id) | SavedDashboard.shared.is_(True),
                )
            )
        ).scalar_one_or_none()
        if d is None:
            raise DashboardNotFound(str(dashboard_id))
        return d

    async def _owned(self, actor: Actor, dashboard_id: uuid.UUID) -> SavedDashboard:
        d = (
            await self.db.execute(
                select(SavedDashboard).where(
                    SavedDashboard.id == dashboard_id,
                    SavedDashboard.tenant_id == actor.tenant_id,
                    SavedDashboard.owner_id == actor.user_id,
                )
            )
        ).scalar_one_or_none()
        if d is None:
            raise DashboardNotFound(str(dashboard_id))
        return d

    async def _name_taken(self, actor: Actor, name: str, exclude: uuid.UUID | None = None) -> bool:
        stmt = select(SavedDashboard.id).where(
            SavedDashboard.tenant_id == actor.tenant_id,
            SavedDashboard.owner_id == actor.user_id,
            SavedDashboard.name == name,
        )
        if exclude is not None:
            stmt = stmt.where(SavedDashboard.id != exclude)
        return (await self.db.execute(stmt.limit(1))).first() is not None

    async def _check_targets(self, actor: Actor, widgets: list[Widget]) -> None:
        """Every sensor / interface a widget names belongs to the caller's tenant."""
        sensor_ids = {uuid.UUID(w.config["sensor_id"]) for w in widgets if w.type == "sensor_chart"}
        interface_ids = {uuid.UUID(w.config["interface_id"]) for w in widgets if w.type == "interface_chart"}
        if sensor_ids:
            found = set(
                (
                    await self.db.execute(
                        select(Sensor.id).where(Sensor.id.in_(sensor_ids), Sensor.tenant_id == actor.tenant_id, Sensor.is_deleted.is_(False))
                    )
                ).scalars()
            )
            if sensor_ids - found:
                raise WidgetTargetInvalid("A sensor chart names a sensor that does not exist in your organization")
        if interface_ids:
            found = set(
                (
                    await self.db.execute(
                        select(Interface.id).where(
                            Interface.id.in_(interface_ids), Interface.tenant_id == actor.tenant_id, Interface.is_deleted.is_(False)
                        )
                    )
                ).scalars()
            )
            if interface_ids - found:
                raise WidgetTargetInvalid("An interface chart names an interface that does not exist in your organization")

    async def create(self, actor: Actor, body: DashboardCreate) -> SavedDashboard:
        count = (
            await self.db.execute(
                select(func.count()).select_from(SavedDashboard).where(
                    SavedDashboard.tenant_id == actor.tenant_id, SavedDashboard.owner_id == actor.user_id
                )
            )
        ).scalar_one()
        if count >= MAX_DASHBOARDS_PER_OWNER:
            raise DashboardConflict(f"A user can keep at most {MAX_DASHBOARDS_PER_OWNER} dashboards")
        if await self._name_taken(actor, body.name):
            raise DashboardConflict(f"You already have a dashboard named '{body.name}'")
        await self._check_targets(actor, body.widgets)
        d = SavedDashboard(
            tenant_id=actor.tenant_id, owner_id=actor.user_id, name=body.name, description=body.description,
            widgets=[w.model_dump(mode="json") for w in body.widgets], shared=body.shared,
        )
        self.db.add(d)
        await self.db.flush()
        await self.audit.record_entity(
            tenant_id=actor.tenant_id, action="dashboard.created", entity_type="saved_dashboard", entity_id=d.id,
            actor_id=actor.user_id, actor_email=actor.email, description=f"Dashboard '{d.name}' created",
            new_values={"name": d.name, "widgets": len(d.widgets), "shared": d.shared},
        )
        await self.db.commit()
        await self.db.refresh(d)
        return d

    async def update(self, actor: Actor, dashboard_id: uuid.UUID, body: DashboardUpdate) -> SavedDashboard:
        d = await self._owned(actor, dashboard_id)
        changes = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None or k == "description"}
        if body.widgets is not None:
            await self._check_targets(actor, body.widgets)
            changes["widgets"] = [w.model_dump(mode="json") for w in body.widgets]
        if "name" in changes and changes["name"] != d.name and await self._name_taken(actor, changes["name"], d.id):
            raise DashboardConflict(f"You already have a dashboard named '{changes['name']}'")
        old: dict = {}
        new: dict = {}
        for field, value in changes.items():
            if getattr(d, field) != value:
                old[field] = len(d.widgets) if field == "widgets" else getattr(d, field)
                new[field] = len(value) if field == "widgets" else value
                setattr(d, field, value)
        if new:
            await self.db.flush()
            await self.audit.record_entity(
                tenant_id=actor.tenant_id, action="dashboard.updated", entity_type="saved_dashboard", entity_id=d.id,
                actor_id=actor.user_id, actor_email=actor.email,
                description=f"Dashboard '{d.name}' changed: {', '.join(sorted(new))}",
                old_values=old, new_values=new,
            )
            await self.db.commit()
            await self.db.refresh(d)
        return d

    async def delete(self, actor: Actor, dashboard_id: uuid.UUID) -> None:
        d = await self._owned(actor, dashboard_id)
        snapshot = {"name": d.name, "widgets": len(d.widgets or []), "shared": d.shared}
        await self.db.delete(d)
        await self.audit.record_entity(
            tenant_id=actor.tenant_id, action="dashboard.deleted", entity_type="saved_dashboard", entity_id=dashboard_id,
            actor_id=actor.user_id, actor_email=actor.email, description=f"Dashboard '{snapshot['name']}' deleted",
            old_values=snapshot,
        )
        await self.db.commit()


__all__ = [
    "DashboardConflict", "DashboardNotFound", "DashboardStore", "WidgetTargetInvalid", "dashboard_out", "dashboard_summary",
]

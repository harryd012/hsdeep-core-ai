"""
Host saved views and bulk host actions (observability plan P2 slice 2).

Saved views: tenant-scoped CRUD with an owner. A view is listed to its owner
and, when `shared`, to everyone in the tenant; only the owner changes it.

Bulk actions on up to MAX_BULK_HOSTS hosts of the caller's tenant:
  * classify: set criticality / environment (or clear it), add / remove tags.
    These are operator-owned CMDB fields; no collector writes them, so a value
    set here is not overwritten by the next sync.
  * monitoring: pause / resume every sensor of the hosts through the P1 sensor
    lifecycle (`SensorService.apply`), so status, alerts and their resolution
    behave exactly as for one sensor. Resume only touches paused sensors, pause
    only active ones: a disabled sensor stays disabled.

Every changed host is one audit event with old and new values; ids that are not
in the caller's tenant are reported as not found and never touched.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.device import Device
from app.models.host_saved_view import MAX_VIEWS_PER_OWNER, HostSavedView
from app.models.sensor import Sensor
from app.repositories.alert_repository import AlertRepository
from app.schemas.host_inventory import (
    MAX_TAGS_PER_HOST,
    HostBulkRequest,
    HostBulkResult,
    HostViewCreate,
    HostViewFilters,
    HostViewOut,
    HostViewUpdate,
)
from app.schemas.monitoring import SensorUpdate
from app.services.alert_service import AlertService
from app.services.audit_service import AuditService
from app.services.monitoring.sensor_service import SensorService

#: Upper bound on sensors touched by one pause / resume request.
MAX_BULK_SENSORS = 5000


class HostViewNotFound(Exception):
    pass


class HostViewConflict(Exception):
    """Name already used by this owner, or the owner's view limit is reached."""


@dataclass(frozen=True)
class Actor:
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    email: str | None


def view_out(view: HostSavedView, actor: Actor) -> HostViewOut:
    return HostViewOut(
        id=view.id,
        name=view.name,
        filters=HostViewFilters.model_validate(view.filters or {}),
        layout=view.layout,
        shared=view.shared,
        owned=view.owner_id == actor.user_id,
        created_at=view.created_at,
        updated_at=view.updated_at,
    )


class HostSavedViewService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.audit = AuditService(db)

    async def list(self, actor: Actor) -> list[HostSavedView]:
        """The caller's own views, then views others shared in the tenant; by name."""
        stmt = (
            select(HostSavedView)
            .where(
                HostSavedView.tenant_id == actor.tenant_id,
                (HostSavedView.owner_id == actor.user_id) | HostSavedView.shared.is_(True),
            )
            .order_by((HostSavedView.owner_id != actor.user_id).asc(), func.lower(HostSavedView.name), HostSavedView.id)
        )
        return list((await self.db.execute(stmt)).scalars())

    async def _owned(self, actor: Actor, view_id: uuid.UUID) -> HostSavedView:
        """The caller's own view. Another user's or tenant's view is 'not found'
        (a shared view is readable through `list`, never writable)."""
        view = (
            await self.db.execute(
                select(HostSavedView).where(
                    HostSavedView.id == view_id,
                    HostSavedView.tenant_id == actor.tenant_id,
                    HostSavedView.owner_id == actor.user_id,
                )
            )
        ).scalar_one_or_none()
        if view is None:
            raise HostViewNotFound(str(view_id))
        return view

    async def _name_taken(self, actor: Actor, name: str, exclude: uuid.UUID | None = None) -> bool:
        stmt = select(HostSavedView.id).where(
            HostSavedView.tenant_id == actor.tenant_id,
            HostSavedView.owner_id == actor.user_id,
            HostSavedView.name == name,
        )
        if exclude is not None:
            stmt = stmt.where(HostSavedView.id != exclude)
        return (await self.db.execute(stmt.limit(1))).first() is not None

    async def create(self, actor: Actor, body: HostViewCreate) -> HostSavedView:
        count = (
            await self.db.execute(
                select(func.count()).select_from(HostSavedView).where(
                    HostSavedView.tenant_id == actor.tenant_id, HostSavedView.owner_id == actor.user_id
                )
            )
        ).scalar_one()
        if count >= MAX_VIEWS_PER_OWNER:
            raise HostViewConflict(f"A user can keep at most {MAX_VIEWS_PER_OWNER} saved views")
        if await self._name_taken(actor, body.name):
            raise HostViewConflict(f"You already have a view named '{body.name}'")
        view = HostSavedView(
            tenant_id=actor.tenant_id,
            owner_id=actor.user_id,
            name=body.name,
            filters=body.filters.model_dump(mode="json", exclude_none=True),
            layout=body.layout,
            shared=body.shared,
        )
        self.db.add(view)
        await self.db.flush()
        await self.audit.record_entity(
            tenant_id=actor.tenant_id, action="host_view.created", entity_type="host_saved_view", entity_id=view.id,
            actor_id=actor.user_id, actor_email=actor.email, description=f"Saved host view '{view.name}' created",
            new_values={"name": view.name, "filters": view.filters, "layout": view.layout, "shared": view.shared},
        )
        await self.db.commit()
        await self.db.refresh(view)
        return view

    async def update(self, actor: Actor, view_id: uuid.UUID, body: HostViewUpdate) -> HostSavedView:
        view = await self._owned(actor, view_id)
        changes = body.model_dump(exclude_unset=True)
        if "filters" in changes and body.filters is not None:
            changes["filters"] = body.filters.model_dump(mode="json", exclude_none=True)
        changes = {k: v for k, v in changes.items() if v is not None}
        if "name" in changes and changes["name"] != view.name and await self._name_taken(actor, changes["name"], view.id):
            raise HostViewConflict(f"You already have a view named '{changes['name']}'")
        old: dict = {}
        new: dict = {}
        for field, value in changes.items():
            if getattr(view, field) != value:
                old[field] = getattr(view, field)
                new[field] = value
                setattr(view, field, value)
        if new:
            await self.db.flush()
            await self.audit.record_entity(
                tenant_id=actor.tenant_id, action="host_view.updated", entity_type="host_saved_view", entity_id=view.id,
                actor_id=actor.user_id, actor_email=actor.email,
                description=f"Saved host view '{view.name}' changed: {', '.join(sorted(new))}",
                old_values=old, new_values=new,
            )
            await self.db.commit()
            await self.db.refresh(view)
        return view

    async def delete(self, actor: Actor, view_id: uuid.UUID) -> None:
        view = await self._owned(actor, view_id)
        snapshot = {"name": view.name, "filters": view.filters, "layout": view.layout, "shared": view.shared}
        await self.db.delete(view)
        await self.audit.record_entity(
            tenant_id=actor.tenant_id, action="host_view.deleted", entity_type="host_saved_view", entity_id=view_id,
            actor_id=actor.user_id, actor_email=actor.email, description=f"Saved host view '{snapshot['name']}' deleted",
            old_values=snapshot,
        )
        await self.db.commit()


class TooManySensors(Exception):
    pass


class HostBulkService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.audit = AuditService(db)

    async def run(self, actor: Actor, req: HostBulkRequest) -> HostBulkResult:
        devices = list(
            (
                await self.db.execute(
                    select(Device)
                    .where(
                        Device.tenant_id == actor.tenant_id,
                        Device.is_deleted.is_(False),
                        Device.id.in_(req.device_ids),
                    )
                    .order_by(Device.id)
                    .with_for_update()
                )
            ).scalars()
        )
        found = {d.id for d in devices}
        result = HostBulkResult(
            action=req.action,
            requested=len(req.device_ids),
            matched=len(devices),
            changed=0,
            not_found=[i for i in req.device_ids if i not in found],
        )
        if req.action in ("pause_monitoring", "resume_monitoring"):
            await self._monitoring(actor, req, devices, result)
        else:
            for device in devices:
                old, new, limited = self._classify(device, req)
                result.tag_limit_hit += limited
                if new:
                    result.changed += 1
                    await self._audit_device(actor, device, req.action, old, new)
        await self.db.commit()
        return result

    @staticmethod
    def _classify(device: Device, req: HostBulkRequest) -> tuple[dict, dict, int]:
        """Apply one classify action to one host; returns (old, new, tag_limit_hit)."""
        if req.action == "set_criticality":
            field, value = "criticality", req.value
        elif req.action == "set_environment":
            field, value = "environment", req.value
        else:
            field = "tags"
            current = [t for t in (device.tags or []) if isinstance(t, str)]
            if req.action == "add_tags":
                additions = [t for t in req.tags if t not in current]
                if len(current) + len(additions) > MAX_TAGS_PER_HOST:
                    return {}, {}, 1
                value = current + additions
            else:
                remove = set(req.tags)
                value = [t for t in current if t not in remove]
            if value == current:
                return {}, {}, 0
            old_tags = list(device.tags or [])
            device.tags = value  # a new list, so the JSONB column is marked dirty
            return {"tags": old_tags}, {"tags": value}, 0
        if getattr(device, field) == value:
            return {}, {}, 0
        old = {field: getattr(device, field)}
        setattr(device, field, value)
        return old, {field: value}, 0

    async def _monitoring(self, actor: Actor, req: HostBulkRequest, devices: list[Device], result: HostBulkResult) -> None:
        pause = req.action == "pause_monitoring"
        source, target = ("active", "paused") if pause else ("paused", "active")
        sensors = list(
            (
                await self.db.execute(
                    select(Sensor)
                    .where(
                        Sensor.tenant_id == actor.tenant_id,
                        Sensor.device_id.in_([d.id for d in devices]),
                        Sensor.admin_state == source,
                    )
                    .order_by(Sensor.device_id, Sensor.id)
                    .limit(MAX_BULK_SENSORS + 1)
                )
            ).scalars()
        )
        if len(sensors) > MAX_BULK_SENSORS:
            raise TooManySensors(f"These hosts have more than {MAX_BULK_SENSORS} sensors to change; select fewer hosts")

        sensor_service = SensorService(self.db)
        alerts = AlertService(AlertRepository(self.db))
        per_device: dict[uuid.UUID, list[str]] = {}
        for sensor in sensors:
            _old, new = await sensor_service.apply(sensor, SensorUpdate(admin_state=target))
            if not new:
                continue
            result.sensors_changed += 1
            per_device.setdefault(sensor.device_id, []).append(sensor.name)
            if "status" in new:
                await alerts.evaluate_sensor(sensor)

        names = {d.id: d for d in devices}
        for device_id, sensor_names in per_device.items():
            result.changed += 1
            await self._audit_device(
                actor, names[device_id], req.action,
                {"sensor_admin_state": source},
                {"sensor_admin_state": target, "sensors": len(sensor_names)},
            )

    async def _audit_device(self, actor: Actor, device: Device, action: str, old: dict, new: dict) -> None:
        await self.audit.record_entity(
            tenant_id=actor.tenant_id,
            action=f"device.bulk.{action}",
            entity_type="device",
            entity_id=device.id,
            actor_id=actor.user_id,
            actor_email=actor.email,
            description=f"Host {device.name}: {action.replace('_', ' ')} (bulk)",
            old_values=old,
            new_values=new,
        )

"""
Repository Pattern: all direct SQLAlchemy access for ServiceNowConnection lives
here. CRUD + connection management queries.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.servicenow import ServiceNowConnection


class ServiceNowConnectionNotFound(Exception):
    pass


class ServiceNowConnectionRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, tenant_id: uuid.UUID, **fields) -> ServiceNowConnection:
        conn = ServiceNowConnection(tenant_id=tenant_id, **fields)
        self.db.add(conn)
        await self.db.flush()
        await self.db.refresh(conn)
        return conn

    async def get(self, conn_id: uuid.UUID, tenant_id: uuid.UUID) -> ServiceNowConnection | None:
        stmt = select(ServiceNowConnection).where(
            ServiceNowConnection.id == conn_id,
            ServiceNowConnection.tenant_id == tenant_id,
            ServiceNowConnection.is_deleted.is_(False),
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_instance_url(self, tenant_id: uuid.UUID, instance_url: str) -> ServiceNowConnection | None:
        """UniqueConstraint guard check before create."""
        stmt = select(ServiceNowConnection).where(
            ServiceNowConnection.tenant_id == tenant_id,
            ServiceNowConnection.instance_url == instance_url,
            ServiceNowConnection.is_deleted.is_(False),
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_active_by_instance_url(self, tenant_id: uuid.UUID, instance_url: str) -> ServiceNowConnection | None:
        """Active connection for a given instance URL."""
        stmt = select(ServiceNowConnection).where(
            ServiceNowConnection.tenant_id == tenant_id,
            ServiceNowConnection.instance_url == instance_url,
            ServiceNowConnection.is_deleted.is_(False),
            ServiceNowConnection.is_active.is_(True),
            ServiceNowConnection.status != "disabled",
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def list_active(self, tenant_id: uuid.UUID) -> list[ServiceNowConnection]:
        """Active (non-disabled) connections for this tenant."""
        stmt = select(ServiceNowConnection).where(
            ServiceNowConnection.tenant_id == tenant_id,
            ServiceNowConnection.is_deleted.is_(False),
            ServiceNowConnection.is_active.is_(True),
            ServiceNowConnection.status != "disabled",
        ).order_by(ServiceNowConnection.name)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def update_status(
        self,
        conn: ServiceNowConnection,
        *,
        status: str | None = None,
        last_error: str | None = None,
        last_sync_at: object | None = None,
    ) -> ServiceNowConnection:
        if status is not None:
            conn.status = status
        if last_error is not None:
            conn.last_error = last_error
        if last_sync_at is not None:
            conn.last_sync_at = last_sync_at
        await self.db.flush()
        await self.db.refresh(conn)
        return conn

    async def update_credentials(
        self,
        conn: ServiceNowConnection,
        *,
        username: str | None = None,
        password_encrypted: str | None = None,
        oauth_client_id: str | None = None,
        oauth_client_secret_encrypted: str | None = None,
        oauth_access_token_encrypted: str | None = None,
        oauth_refresh_token_encrypted: str | None = None,
        webhook_secret_encrypted: str | None = None,
    ) -> ServiceNowConnection:
        """Update credential fields (always ciphertext) without touching the rest."""
        if username is not None:
            conn.username = username
        if password_encrypted is not None:
            conn.password_encrypted = password_encrypted
        if oauth_client_id is not None:
            conn.oauth_client_id = oauth_client_id
        if oauth_client_secret_encrypted is not None:
            conn.oauth_client_secret_encrypted = oauth_client_secret_encrypted
        if oauth_access_token_encrypted is not None:
            conn.oauth_access_token_encrypted = oauth_access_token_encrypted
        if oauth_refresh_token_encrypted is not None:
            conn.oauth_refresh_token_encrypted = oauth_refresh_token_encrypted
        if webhook_secret_encrypted is not None:
            conn.webhook_secret_encrypted = webhook_secret_encrypted
        await self.db.flush()
        await self.db.refresh(conn)
        return conn

    async def update_rate_limit(self, conn: ServiceNowConnection, rpm: int) -> ServiceNowConnection:
        conn.rate_limit_requests_per_minute = rpm
        await self.db.flush()
        await self.db.refresh(conn)
        return conn

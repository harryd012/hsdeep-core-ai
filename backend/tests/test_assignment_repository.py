"""
Assignment Repository unit tests.

Covers the Enterprise Assignment Engine's repository layer:
  - AssignmentGroup CRUD (tenant-scoped)
  - AssignmentGroupMember CRUD (tenant-scoped, unique per group+user)
  - get_active_members() routing helper
  - count_open_tickets_for_user() / count_open_tickets_for_group() for the
    least_loaded routing strategy

All tests run against the real PostgreSQL database (same isolation strategy
as conftest.py: committed rows scoped to a unique Tenant, cleaned up by
cascade delete on Tenant teardown). Users are created directly via the ORM
same as other tests.
"""
from __future__ import annotations

import uuid

import pytest
import pytest_asyncio

from app.core.db import AsyncSessionLocal
from app.models.user import User
from app.models.ticket import Ticket
from app.repositories.assignment_repository import AssignmentRepository

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _make_user(tenant_id: uuid.UUID, *, email: str | None = None) -> User:
    """Create and commit a User scoped to `tenant`; cleaned up by cascade."""
    async with AsyncSessionLocal() as db:
        user = User(
            tenant_id=tenant_id,
            email=email or f"user-{uuid.uuid4().hex[:8]}@hsdeep.test",
            full_name="Test Engineer",
            is_active=True,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
    return user


# ---------------------------------------------------------------------------
# AssignmentGroup CRUD
# ---------------------------------------------------------------------------

class TestAssignmentGroupRepository:

    async def test_create_group(self, tenant):
        async with AsyncSessionLocal() as db:
            repo = AssignmentRepository(db)
            group = await repo.create_group(
                tenant_id=tenant.id,
                name="Network Team",
                description="Network operations engineers",
                strategy="round_robin",
            )
            await db.commit()

        assert group.id is not None
        assert group.name == "Network Team"
        assert group.strategy == "round_robin"
        assert group.is_active is True

    async def test_get_group_tenant_scoped(self, tenant):
        """Tenant A's group must NOT be visible to tenant B."""
        async with AsyncSessionLocal() as db:
            repo = AssignmentRepository(db)
            group = await repo.create_group(tenant_id=tenant.id, name="General")
            await db.commit()

        other_tenant_id = uuid.uuid4()
        async with AsyncSessionLocal() as db:
            repo = AssignmentRepository(db)
            found = await repo.get_group(group.id, tenant.id)
            wrong = await repo.get_group(group.id, other_tenant_id)

        assert found is not None
        assert found.id == group.id
        assert wrong is None

    async def test_get_group_by_name(self, tenant):
        async with AsyncSessionLocal() as db:
            repo = AssignmentRepository(db)
            await repo.create_group(tenant_id=tenant.id, name="General")
            await db.commit()

        async with AsyncSessionLocal() as db:
            repo = AssignmentRepository(db)
            group = await repo.get_group_by_name(tenant.id, "General")

        assert group is not None
        assert group.name == "General"

    async def test_list_groups_only_own_tenant(self, tenant):
        async with AsyncSessionLocal() as db:
            repo = AssignmentRepository(db)
            await repo.create_group(tenant_id=tenant.id, name="General")
            await repo.create_group(tenant_id=tenant.id, name="Network Team")
            await db.commit()

        other_tenant_id = uuid.uuid4()
        async with AsyncSessionLocal() as db:
            repo = AssignmentRepository(db)
            mine = await repo.list_groups(tenant.id)
            theirs = await repo.list_groups(other_tenant_id)

        assert len(mine) >= 2
        assert {g.name for g in mine} >= {"General", "Network Team"}
        assert theirs == []

    async def test_update_group(self, tenant):
        async with AsyncSessionLocal() as db:
            repo = AssignmentRepository(db)
            group = await repo.create_group(tenant_id=tenant.id, name="General")
            await db.commit()

        async with AsyncSessionLocal() as db:
            repo = AssignmentRepository(db)
            # Re-fetch the group in THIS session so the ORM object is attached
            # (flush/refresh on a detached object from a prior session fails).
            attached = await repo.get_group(group.id, tenant.id)
            updated = await repo.update_group(attached, strategy="least_loaded", is_active=False)
            await db.commit()

        assert updated.strategy == "least_loaded"
        assert updated.is_active is False

    async def test_list_groups_filter_is_active(self, tenant):
        async with AsyncSessionLocal() as db:
            repo = AssignmentRepository(db)
            await repo.create_group(tenant_id=tenant.id, name="Active Group")
            await repo.create_group(tenant_id=tenant.id, name="Inactive Group", is_active=False)
            await db.commit()

        async with AsyncSessionLocal() as db:
            repo = AssignmentRepository(db)
            active_only = await repo.list_groups(tenant.id, is_active=True)
            all_groups = await repo.list_groups(tenant.id)

        assert all(g.is_active for g in active_only)
        active_names = {g.name for g in active_only}
        assert "Active Group" in active_names
        assert "Inactive Group" not in active_names
        assert len(all_groups) >= 2


# ---------------------------------------------------------------------------
# AssignmentGroupMember CRUD
# ---------------------------------------------------------------------------

class TestAssignmentGroupMemberRepository:

    async def test_add_member(self, tenant):
        user = await _make_user(tenant.id)

        async with AsyncSessionLocal() as db:
            repo = AssignmentRepository(db)
            group = await repo.create_group(tenant_id=tenant.id, name="General")
            member = await repo.add_member(
                tenant_id=tenant.id,
                group_id=group.id,
                user_id=user.id,
                skills_json=["network", "cisco"],
                capacity_weight=2,
            )
            await db.commit()

        assert member.id is not None
        assert member.group_id == group.id
        assert member.user_id == user.id
        assert member.skills_json == ["network", "cisco"]
        assert member.capacity_weight == 2
        assert member.is_active is True

    async def test_member_unique_per_group_and_user(self, tenant):
        """Adding the same user twice to the same group must raise IntegrityError."""
        user = await _make_user(tenant.id)

        async with AsyncSessionLocal() as db:
            repo = AssignmentRepository(db)
            group = await repo.create_group(tenant_id=tenant.id, name="General")
            await repo.add_member(tenant_id=tenant.id, group_id=group.id, user_id=user.id)
            await db.commit()

        from sqlalchemy.exc import IntegrityError

        with pytest.raises(IntegrityError):
            async with AsyncSessionLocal() as db:
                repo = AssignmentRepository(db)
                await repo.add_member(tenant_id=tenant.id, group_id=group.id, user_id=user.id)
                await db.commit()

    async def test_get_active_members(self, tenant):
        """Only active (is_active=True, not deleted) members are returned."""
        user_a = await _make_user(tenant.id)
        user_b = await _make_user(tenant.id)
        user_c = await _make_user(tenant.id)

        async with AsyncSessionLocal() as db:
            repo = AssignmentRepository(db)
            group = await repo.create_group(tenant_id=tenant.id, name="General")
            await repo.add_member(tenant_id=tenant.id, group_id=group.id, user_id=user_a.id)
            await repo.add_member(tenant_id=tenant.id, group_id=group.id, user_id=user_b.id)
            inactive = await repo.add_member(
                tenant_id=tenant.id, group_id=group.id, user_id=user_c.id, is_active=False
            )
            await db.commit()

        async with AsyncSessionLocal() as db:
            repo = AssignmentRepository(db)
            active = await repo.get_active_members(group.id)

        active_user_ids = {m.user_id for m in active}
        assert user_a.id in active_user_ids
        assert user_b.id in active_user_ids
        assert user_c.id not in active_user_ids  # inactive member excluded

    async def test_remove_member_soft_deletes(self, tenant):
        user = await _make_user(tenant.id)

        async with AsyncSessionLocal() as db:
            repo = AssignmentRepository(db)
            group = await repo.create_group(tenant_id=tenant.id, name="General")
            member = await repo.add_member(tenant_id=tenant.id, group_id=group.id, user_id=user.id)
            await repo.remove_member(member)
            await db.commit()

        async with AsyncSessionLocal() as db:
            repo = AssignmentRepository(db)
            found = await repo.get_member_by_group_and_user(group.id, user.id, tenant.id)

        assert found is None  # soft-deleted row is excluded from queries

    async def test_member_tenant_isolation(self, tenant):
        """A member from tenant A must not be visible to tenant B."""
        user = await _make_user(tenant.id)

        async with AsyncSessionLocal() as db:
            repo = AssignmentRepository(db)
            group = await repo.create_group(tenant_id=tenant.id, name="General")
            member = await repo.add_member(tenant_id=tenant.id, group_id=group.id, user_id=user.id)
            await db.commit()

        other_tenant_id = uuid.uuid4()
        async with AsyncSessionLocal() as db:
            repo = AssignmentRepository(db)
            found = await repo.get_member(member.id, other_tenant_id)

        assert found is None


# ---------------------------------------------------------------------------
# Routing helpers (least_loaded strategy)
# ---------------------------------------------------------------------------

class TestRoutingHelpers:

    async def test_count_open_tickets_for_user(self, tenant):
        """Non-terminal tickets assigned to a user are counted; terminal
        statuses (resolved/closed/cancelled) are excluded."""
        user = await _make_user(tenant.id)

        async with AsyncSessionLocal() as db:
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc)
            # Two open tickets + one resolved ticket
            t1 = Ticket(
                tenant_id=tenant.id,
                ticket_number=f"TCK-{uuid.uuid4().hex[:6]}",
                title="T1", status="open",
                assigned_to=user.id,
                opened_at=now,
                created_at=now,
                updated_at=now,
            )
            t2 = Ticket(
                tenant_id=tenant.id,
                ticket_number=f"TCK-{uuid.uuid4().hex[:6]}",
                title="T2", status="in_progress",
                assigned_to=user.id,
                opened_at=now,
                created_at=now,
                updated_at=now,
            )
            t3 = Ticket(
                tenant_id=tenant.id,
                ticket_number=f"TCK-{uuid.uuid4().hex[:6]}",
                title="T3", status="resolved",
                assigned_to=user.id,
                opened_at=now,
                created_at=now,
                updated_at=now,
            )
            db.add_all([t1, t2, t3])
            await db.commit()

        async with AsyncSessionLocal() as db:
            repo = AssignmentRepository(db)
            count = await repo.count_open_tickets_for_user(user.id, tenant.id)

        assert count == 2  # t1 and t2 only

    async def test_count_open_tickets_for_group(self, tenant):
        """Non-terminal tickets routed to a group are counted."""
        async with AsyncSessionLocal() as db:
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc)
            repo = AssignmentRepository(db)
            group = await repo.create_group(tenant_id=tenant.id, name="General")
            await db.commit()

            t1 = Ticket(
                tenant_id=tenant.id,
                ticket_number=f"TCK-{uuid.uuid4().hex[:6]}",
                title="T1", status="open",
                assigned_group_id=group.id,
                opened_at=now,
                created_at=now,
                updated_at=now,
            )
            t2 = Ticket(
                tenant_id=tenant.id,
                ticket_number=f"TCK-{uuid.uuid4().hex[:6]}",
                title="T2", status="closed",
                assigned_group_id=group.id,
                opened_at=now,
                created_at=now,
                updated_at=now,
            )
            db.add_all([t1, t2])
            await db.commit()

        async with AsyncSessionLocal() as db:
            repo = AssignmentRepository(db)
            count = await repo.count_open_tickets_for_group(group.id, tenant.id)

        assert count == 1  # t1 only (t2 is closed)

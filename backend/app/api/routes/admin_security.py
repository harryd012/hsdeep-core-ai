"""
Platform security status for operators (enterprise security programme S4).

  GET /api/admin/security/encryption   which key protects how many stored secrets

Platform superusers only (require_admin). Reports counts and key fingerprints,
never a secret. Rotation itself runs from scripts/rotate_encryption_keys.py so
it is an explicit operator action with the new key already deployed.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import Principal, require_admin
from app.core.db import get_db
from app.services.key_rotation import encryption_status

router = APIRouter(prefix="/admin/security", tags=["platform-admin"])


@router.get("/encryption")
async def encryption(
    _: Principal = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return (await encryption_status(db)).as_dict()

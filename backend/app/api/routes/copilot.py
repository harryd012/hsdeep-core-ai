"""
AI Copilot API.

POST /copilot/chat — one Copilot turn over free text. Tenant is derived
from the authenticated principal (same pattern as every other router).
The request handler owns db.commit() (project convention) — the Copilot
router service commits inside the command path before returning, so the
route simply forwards its result. Query turns never write.

See app/services/copilot_router.py for the intent decision logic.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_tenant_id, require_permission
from app.core.db import get_db
from app.schemas.copilot import CopilotChatRequest, CopilotChatResponse
from app.services.copilot_router import CopilotRouter

import uuid

router = APIRouter(prefix="/copilot", tags=["copilot"])


def get_copilot_router(db: AsyncSession = Depends(get_db)) -> CopilotRouter:
    return CopilotRouter(db)


@router.post("/chat", response_model=CopilotChatResponse, dependencies=[Depends(require_permission("incidents.read"))])
async def copilot_chat(
    payload: CopilotChatRequest,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    copilot: CopilotRouter = Depends(get_copilot_router),
):
    """Answer a free-text message: either create + route a real Task
    (command intent) or summarize real platform data (query intent). The
    response always includes context_used for audit/debuggability."""
    return await copilot.handle_message(tenant_id, payload.message)

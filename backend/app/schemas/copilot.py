"""Pydantic schemas for the AI Copilot chat endpoint.

Kept separate from command_center.py schemas because the Copilot is an
orchestration surface over several services (CommandCenterService,
AIInsightService, ITSM repos) - its wire shape is not a Command Center
aggregate.
"""
from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field


class CopilotChatRequest(BaseModel):
    """Free-text message from the Copilot bar / ai-copilot page."""

    message: str = Field(min_length=1, max_length=1000)


class CopilotChatResponse(BaseModel):
    """Result of one Copilot turn.

    task_created=False with task_id=None means this was answered as a
    query (or the command could not be routed) - no Task row was written.
    context_used is the exact dict that was sent to / derived from the
    LLM, for audit/debuggability.
    """

    answer: str
    task_created: bool = False
    task_id: uuid.UUID | None = None
    task_number: str | None = None
    department: str | None = None
    agent: str | None = None
    intent: str = "query"
    context_used: dict[str, Any] = {}

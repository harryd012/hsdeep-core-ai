"""
Shared pagination envelope for list endpoints that need a total count
alongside their page of results (tables with server-side pagination -
Infrastructure, Alerts, Monitoring Sources, etc). Endpoints that don't need
a total count (e.g. the existing /api/devices, /api/alerts) are unaffected -
this is additive, opt-in per route, not a replacement for existing list
response shapes.
"""
from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int

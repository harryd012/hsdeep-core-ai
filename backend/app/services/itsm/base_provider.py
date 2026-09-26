"""
Base ITSM Provider abstraction (Phase 1).

Defines:
  * ProviderExceptions - ITSMProviderError (base), ITSMAuthError,
    ITSMHTTPError - for structured error handling.
  * Dataclasses - ITSMTicketCreate, ITSMTicketUpdate, ITSMTicketResponse,
    ITSMCommentCreate, ITSMHealthCheckResult - provider-agnostic
    data transfer objects reused by every provider implementation.
  * BaseITSMProvider - the abstract adapter interface every ITSM
    provider (ServiceNow, Jira, Freshservice, etc.) implements.

This mirrors the existing collector base-class pattern (collectors/base.py)
but for ITSM providers: one abstract interface, multiple concrete
implementations selected at runtime via the ProviderRegistry/Factory.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, ClassVar

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------

class ITSMProviderError(Exception):
    """Base exception for all ITSM provider errors."""

    def __init__(
        self,
        message: str,
        *,
        provider_type: str | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.provider_type = provider_type
        self.status_code = status_code

    def __str__(self) -> str:
        parts = [super().__str__()]
        if self.provider_type:
            parts.append(f"provider={self.provider_type}")
        if self.status_code:
            parts.append(f"status_code={self.status_code}")
        return " ".join(parts)


class ITSMAuthError(ITSMProviderError):
    """Raised when authentication credentials are invalid or expired."""
    pass


class ITSMHTTPError(ITSMProviderError):
    """Raised when an upstream HTTP request fails."""
    pass


class ITSMWebhookError(ITSMProviderError):
    """Raised when webhook signature verification or processing fails."""
    pass


# ---------------------------------------------------------------------------
# Dataclasses (provider-agnostic DTOs)
# ---------------------------------------------------------------------------

@dataclass
class ITSMTicketCreate:
    """Payload for creating a ticket in an external ITSM system."""
    title: str
    description: str | None = None
    priority: str | None = None
    category: str | None = None
    subcategory: str | None = None
    assignment_group: str | None = None
    assigned_to: str | None = None
    correlation_id: str | None = None
    due_date: datetime | None = None
    impact: int | None = None
    urgency: int | None = None
    custom_fields: dict[str, Any] = field(default_factory=dict)


@dataclass
class ITSMTicketUpdate:
    """Payload for updating an external ticket. All fields optional."""
    title: str | None = None
    description: str | None = None
    priority: str | None = None
    category: str | None = None
    subcategory: str | None = None
    assignment_group: str | None = None
    assigned_to: str | None = None
    status: str | None = None
    due_date: datetime | None = None
    custom_fields: dict[str, Any] | None = None


@dataclass
class ITSMTicketResponse:
    """Normalised response from an ITSM provider after create/update/get."""
    external_id: str
    external_number: str | None = None
    title: str | None = None
    description: str | None = None
    priority: str | None = None
    status: str | None = None
    category: str | None = None
    subcategory: str | None = None
    assignment_group: str | None = None
    assigned_to: str | None = None
    correlation_id: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    sla_due_at: datetime | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class ITSMCommentCreate:
    """Payload for adding a comment/work note to an external ticket."""
    body: str
    is_internal: bool = True


@dataclass
class ITSMHealthCheckResult:
    """Result of a connection health check."""
    healthy: bool
    message: str
    latency_ms: float | None = None
    provider_type: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class ITSMWebhookResult:
    """Normalised result of parsing a provider webhook callback."""
    external_id: str
    external_number: str | None = None
    status: str | None = None
    priority: str | None = None
    assigned_to: str | None = None
    assigned_team: str | None = None
    correlation_id: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Abstract provider base
# ---------------------------------------------------------------------------

class BaseITSMProvider(ABC):
    """Abstract base class for all ITSM provider implementations.

    Each concrete provider (ServiceNowProvider, JiraProvider, etc.)
    subclasses this and implements the abstract methods. The
    ConnectorManager instantiates providers via the ProviderFactory
    and calls these methods - it never calls external APIs directly.

    Provider metadata is declared as ClassVar so it is available on the
    class itself (for the registry) without instantiation.

    Follows the same pattern as collectors/base.BaseCollector.
    """

    provider_type: ClassVar[str] = ""
    display_name: ClassVar[str] = ""
    supported_auth_modes: ClassVar[list[str]] = []
    capabilities: ClassVar[dict[str, bool]] = {}

    def __init__(
        self,
        instance_url: str,
        auth_type: str,
        credentials: dict[str, Any],
        rate_limit_rpm: int = 100,
        config: dict[str, Any] | None = None,
    ) -> None:
        self.instance_url = instance_url.rstrip("/")
        self.auth_type = auth_type
        self.credentials = credentials
        self.rate_limit_rpm = rate_limit_rpm
        self.config = config or {}

    # -- Connection / authentication --

    async def refresh_token(self) -> dict[str, Any]:
        """Refresh OAuth2 token. Override in providers that support OAuth2."""
        return {}

    @abstractmethod
    async def test_connection(self) -> ITSMHealthCheckResult:
        """Test connectivity. Return a health-check result."""
        ...

    # -- Priority / status mapping --

    @abstractmethod
    def map_priority_to_external(self, priority: str) -> str:
        """Map internal priority (P1-P4) to provider-specific priority."""
        ...

    @abstractmethod
    def map_priority_from_external(self, external_priority: str) -> str:
        """Map provider-specific priority to internal priority (P1-P4)."""
        ...

    @abstractmethod
    def map_status_from_external(self, external_status: str) -> str:
        """Map provider-specific status to internal status."""
        ...

    @abstractmethod
    def map_status_to_external(self, status: str) -> str:
        """Map internal status to provider-specific status."""
        ...

    # -- Ticket CRUD --

    @abstractmethod
    async def create_ticket(self, payload: ITSMTicketCreate) -> ITSMTicketResponse:
        """Create a ticket (incident/issue) in the external system."""
        ...

    @abstractmethod
    async def get_ticket(self, external_id: str) -> ITSMTicketResponse:
        """Retrieve a ticket by its external ID."""
        ...

    async def update_ticket(self, external_id: str, payload: ITSMTicketUpdate) -> ITSMTicketResponse:
        """Update a ticket. Not all providers support this; override."""
        raise NotImplementedError(f"{self.provider_type} does not support update_ticket")

    async def close_ticket(self, external_id: str, resolution_note: str | None = None) -> ITSMTicketResponse:
        """Close a ticket. Not all providers support this; override."""
        raise NotImplementedError(f"{self.provider_type} does not support close_ticket")

    async def list_tickets(
        self,
        *,
        query: str | None = None,
        limit: int = 50,
        updated_since: datetime | None = None,
    ) -> list[ITSMTicketResponse]:
        """List tickets (for polling/sync). Override if supported."""
        raise NotImplementedError(f"{self.provider_type} does not support list_tickets")

    # -- Comments and attachments --

    async def add_comment(self, external_id: str, payload: ITSMCommentCreate) -> dict[str, Any]:
        """Add a comment/work note to an external ticket."""
        raise NotImplementedError(f"{self.provider_type} does not support add_comment")

    async def add_attachment(self, external_id: str, filename: str, content: bytes, content_type: str | None = None) -> dict[str, Any]:
        """Upload an attachment to an external ticket."""
        raise NotImplementedError(f"{self.provider_type} does not support add_attachment")

    # -- Webhooks --

    async def verify_webhook_signature(self, body: bytes, signature_header: str, secret: str) -> bool:
        """Verify a webhook signature. Override if the provider sends webhooks."""
        return False

    async def parse_webhook_payload(self, body: bytes) -> ITSMWebhookResult:
        """Parse a webhook payload into the provider-agnostic format."""
        raise NotImplementedError(f"{self.provider_type} does not support webhook parsing")

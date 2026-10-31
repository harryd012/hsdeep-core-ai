"""Outbound email: transports, the outbox and message templates."""
from app.services.email.outbox import EmailDisabled, deliver_due, enqueue_email
from app.services.email.transport import EmailMessage, get_transport

__all__ = ["EmailDisabled", "EmailMessage", "deliver_due", "enqueue_email", "get_transport"]

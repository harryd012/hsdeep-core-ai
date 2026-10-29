"""
Email transports. SMTP works with any provider (SES SMTP, Postmark,
SendGrid, Mailgun, SMTP2GO); it uses the standard library in a worker
thread so no extra dependency is needed. LogTransport is for development:
it logs who would receive what, never the message body (which carries the
one-time link).
"""
from __future__ import annotations

import asyncio
import logging
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage as _MimeMessage
from typing import Protocol

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EmailMessage:
    to: str
    subject: str
    body_text: str


class EmailTransport(Protocol):
    async def send(self, message: EmailMessage) -> None: ...


class LogTransport:
    async def send(self, message: EmailMessage) -> None:
        logger.info("email (log transport, not sent): to=%s subject=%r", message.to, message.subject)


class SmtpTransport:
    def __init__(self, cfg: Settings):
        self._cfg = cfg

    def _send_sync(self, message: EmailMessage) -> None:
        mime = _MimeMessage()
        mime["From"] = self._cfg.email_from
        mime["To"] = message.to
        mime["Subject"] = message.subject
        mime.set_content(message.body_text)
        with smtplib.SMTP(self._cfg.smtp_host, self._cfg.smtp_port, timeout=30) as smtp:
            if self._cfg.smtp_starttls:
                smtp.starttls(context=ssl.create_default_context())
            if self._cfg.smtp_username:
                smtp.login(self._cfg.smtp_username, self._cfg.smtp_password)
            smtp.send_message(mime)

    async def send(self, message: EmailMessage) -> None:
        await asyncio.to_thread(self._send_sync, message)


def get_transport(cfg: Settings | None = None) -> EmailTransport | None:
    """The configured transport, or None when email is switched off."""
    cfg = cfg or get_settings()
    if cfg.email_transport == "smtp":
        return SmtpTransport(cfg)
    if cfg.email_transport == "log":
        return LogTransport()
    return None

"""Incident notifications (email, Slack, PagerDuty)."""
from app.services.notifications.dispatcher import NotificationDispatcher, is_allowed_slack_webhook

__all__ = ["NotificationDispatcher", "is_allowed_slack_webhook"]

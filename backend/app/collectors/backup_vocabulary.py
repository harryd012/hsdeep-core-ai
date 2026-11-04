"""
One status vocabulary for every backup provider
(docs/engineering/azure-backup-monitoring.md, "Provider-neutral model").

Each backup collector maps its vendor's words onto these values in
``normalize()``; the saver passes every item and job through ``canonical()``
before storing it, so a value a connector did not map is stored as "other"
instead of leaking a vendor term into the API, the RPO rule or the alerts.
"""
from __future__ import annotations

#: What a storage location is: a cloud vault (Azure Recovery Services, AWS
#: Backup vault), a backup repository (Veeam) or an object storage bucket.
STORAGE_KINDS = ("vault", "repository", "bucket")

#: Whether the provider is protecting an item.
PROTECTION_STATES = (
    "protected",           # backups run on schedule
    "protection_error",    # protection configured but failing
    "ir_pending",          # configured, first backup not run yet
    "protection_stopped",  # stopped; restore points may be kept
    "protection_paused",   # suspended; resumes later
    "other",
)
#: Protection states in which an item is expected to meet the RPO.
ACTIVE_PROTECTION = ("protected", "protection_error", "ir_pending")
STOPPED_PROTECTION = ("protection_stopped", "protection_paused")

#: The provider's verdict on an item's last backup.
BACKUP_RESULTS = ("completed", "completed_with_warnings", "failed", "in_progress", "other")

#: A backup or restore job's state.
JOB_STATUSES = ("completed", "completed_with_warnings", "failed", "in_progress", "cancelled", "other")


def canonical(value: str | None, allowed: tuple[str, ...]) -> str | None:
    """``value`` if it is in the vocabulary, "other" if not; None stays None."""
    if value is None:
        return None
    return value if value in allowed else "other"


__all__ = [
    "ACTIVE_PROTECTION",
    "BACKUP_RESULTS",
    "JOB_STATUSES",
    "PROTECTION_STATES",
    "STOPPED_PROTECTION",
    "STORAGE_KINDS",
    "canonical",
]

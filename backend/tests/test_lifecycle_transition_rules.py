"""
Permanent Phase 1 lifecycle transition-rule test suite.
Validates the resolved-target guard, resolved-as-current normalization,
closed→reopened rejection, and recovered→reopened success — the exact
behavior introduced in the `_validate_transition` change to
`backend/app/services/incident_lifecycle_service.py`.

No DB needed — all assertions are pure-Python calls against the
`_validate_transition` classmethod and `_normalize_status`.
"""

from __future__ import annotations

import pytest

from app.services.incident_lifecycle_service import (
    IncidentLifecycleError,
    IncidentLifecycleService as S,
)


class TestValidatedTransitionRules:
    """Resolution-of-`resolved`, closed→reopened exclusion, and normal paths."""

    def test_resolved_target_raises(self):
        """Per Option A: `resolved` as a write TARGET must raise a clear error."""
        with pytest.raises(IncidentLifecycleError) as excinfo:
            S._validate_transition("open", "resolved")
        msg = str(excinfo.value)
        assert "legacy read-only status" in msg
        assert "recovered" in msg

    def test_resolved_as_current_normalizes_to_recovered(self):
        """`resolved` passed as *current* should normalize to `recovered` rules."""
        # resolved->closed: allowed via recovered normalization
        S._validate_transition("resolved", "closed")
        # resolved->reopened: allowed via recovered normalization
        S._validate_transition("resolved", "reopened")
        # resolved->archived: allowed via recovered normalization
        S._validate_transition("resolved", "archived")

    def test_closed_to_reopened_excluded(self):
        """`closed -> reopened` is deliberately EXCLUDED — it would break the
        "closed is a done-ledger invariant" contract."""
        with pytest.raises(IncidentLifecycleError) as excinfo:
            S._validate_transition("closed", "reopened")
        msg = str(excinfo.value)
        assert "not allowed" in msg or "Lifecycle transition" in msg

    def test_recovered_to_reopened_allowed(self):
        """The intentional re-entry path: a recovered incident can reopen."""
        S._validate_transition("recovered", "reopened")

    def test_investigating_to_recovered_allowed(self):
        """Normal forward path still works."""
        S._validate_transition("investigating", "recovered")

    def test_closed_to_archived_allowed(self):
        """Closed can still go to archived (the only onward path)."""
        S._validate_transition("closed", "archived")

    def test_normal_forbidden_path_still_raises(self):
        """A genuinely illegal transition still raises."""
        with pytest.raises(IncidentLifecycleError):
            S._validate_transition("closed", "open")  # closed->open is forbidden
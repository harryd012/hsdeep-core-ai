"""
Alert Normalization & Correlation Engine.

This package sits alongside `app/services/alert_service.py` (the pre-
existing Alert Engine) rather than replacing it: `AlertService` still owns
the create/update/acknowledge/resolve lifecycle and the alert-storm-
prevention invariants (partial unique indexes on sensor_id / signal
identity). This package adds the layer ABOVE a single alert - severity
mapping (extracted here to remove duplication), event classification, and
cross-alert correlation/incident grouping - and is invoked BY
`AlertService` after an alert is created or updated, never in parallel
with it.
"""

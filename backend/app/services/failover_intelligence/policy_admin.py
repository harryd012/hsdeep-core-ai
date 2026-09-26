"""
Operational SLA policy administration — the tenant-scoped WRITE side of the one
policy store the failover detector evaluates.

This module validates, persists and audits policies. It deliberately does NOT
evaluate anything: ``policies.resolve_policies`` remains the only decision path,
so a policy change can only change *which* thresholds apply — never *how* a
breach is decided. There is no second rule engine here, and no policy edit can
bypass the detector's hysteresis, dwell counter or verification semantics.

WHY VALIDATION LIVES HERE (and not only in the API route)
---------------------------------------------------------
The same rules must hold for the API, for tests and for any future importer, and
a stored policy that cannot be evaluated honestly is worse than a rejected
request. So these are refused with a named field:

* **no threshold at all** — the policy can never breach, so it would look like
  coverage while providing none;
* **contradictory levels** — for higher-is-worse metrics `critical` must be above
  `warning`; for availability (`LOWER_IS_WORSE`) it must be below. A "critical"
  that is easier to cross than "warning" makes the warning level dead;
* **equal warning/critical** — the warning level could never be reported;
* **a `clear` beyond the entry threshold** — recovery would be harder than
  breach, so hysteresis could never clear and the detector would latch;
* **values outside the metric's domain** — percentages are 0..100, latencies and
  jitter are >= 0. ``NaN``/``inf`` are never accepted;
* **a ``scope_id`` that does not exist in this tenant** — the policy would
  silently match no link, so the operator would believe they are covered when
  they are not. Cross-tenant ids fail this check too: they are simply "not in
  this tenant", so nothing about another tenant is disclosed;
* **a duplicate name** — an operator list with two identical names is unusable;
* **two active policies for the same metric at the same scope** — the resolver
  returns exactly one policy per metric, so the second would be dead config and
  the resolved answer would depend on row order.
"""
from __future__ import annotations

import logging
import math
import uuid
from typing import Any

from app.models.operational_sla_policy import (
    LOWER_IS_WORSE,
    METRIC_AVAILABILITY_PCT,
    METRIC_JITTER_MS,
    METRIC_LATENCY_MS,
    METRIC_PACKET_LOSS_PCT,
    METRIC_UTILIZATION_PCT,
    METRICS,
    SCOPE_ALL_WAN,
    SCOPE_LINK,
    SCOPE_SITE,
    SCOPES,
    OperationalSLAPolicy,
)
from app.repositories.operational_sla_policy_repository import (
    OperationalSLAPolicyRepository,
)

logger = logging.getLogger(__name__)

#: Matches the column width; longer names would be truncated by the database.
NAME_MAX_LENGTH = 150

#: Consecutive-sample dwell bounds. The floor is "1 cycle" (the detector's own
#: minimum); the ceiling is a guard against a typo that would effectively disable
#: a policy while appearing configured (~16h at a 10-minute collection cadence).
MIN_SAMPLES_FLOOR = 1
MIN_SAMPLES_CEILING = 100

#: Metrics expressed as a percentage of a whole.
PERCENT_METRICS: frozenset[str] = frozenset(
    {METRIC_AVAILABILITY_PCT, METRIC_PACKET_LOSS_PCT, METRIC_UTILIZATION_PCT}
)
#: Metrics expressed as a duration in milliseconds.
LATENCY_METRICS: frozenset[str] = frozenset({METRIC_LATENCY_MS, METRIC_JITTER_MS})

_THRESHOLD_FIELDS: tuple[str, ...] = (
    "warning_threshold",
    "critical_threshold",
    "clear_threshold",
)


class PolicyValidationError(ValueError):
    """A policy that cannot be evaluated honestly. Names the offending field."""

    def __init__(self, message: str, *, field: str = "policy") -> None:
        super().__init__(message)
        self.field = field
        self.message = message


class PolicyConflictError(ValueError):
    """A policy that would make the resolved answer ambiguous or unusable."""

    def __init__(self, message: str, *, field: str = "policy") -> None:
        super().__init__(message)
        self.field = field
        self.message = message


def normalise_name(name: str | None) -> str:
    """Trimmed name, or raise. Whitespace-only is not a name."""
    cleaned = " ".join(str(name or "").split())
    if not cleaned:
        raise PolicyValidationError(
            "name is required and cannot be blank", field="name"
        )
    if len(cleaned) > NAME_MAX_LENGTH:
        raise PolicyValidationError(
            f"name must be at most {NAME_MAX_LENGTH} characters",
            field="name",
        )
    return cleaned


def validate_metric(metric: str | None) -> str:
    value = str(metric or "").strip().lower()
    if value not in METRICS:
        raise PolicyValidationError(
            f"unknown metric={metric!r}; expected one of {sorted(METRICS)}",
            field="metric",
        )
    return value


def validate_min_samples(value: Any) -> int:
    """Consecutive-cycle dwell, bounded so a typo cannot silently disable a policy."""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise PolicyValidationError(
            "min_consecutive_samples must be an integer",
            field="min_consecutive_samples",
        ) from None
    if parsed < MIN_SAMPLES_FLOOR or parsed > MIN_SAMPLES_CEILING:
        raise PolicyValidationError(
            "min_consecutive_samples must be between "
            f"{MIN_SAMPLES_FLOOR} and {MIN_SAMPLES_CEILING}; got {parsed}",
            field="min_consecutive_samples",
        )
    return parsed


def validate_scope(*, scope: str | None, scope_id: uuid.UUID | None) -> str:
    """Scope and its (mandatory or forbidden) target, kept mutually consistent."""
    value = str(scope or "").strip().lower()
    if value not in SCOPES:
        raise PolicyValidationError(
            f"unknown scope={scope!r}; expected one of {sorted(SCOPES)}",
            field="scope",
        )
    if value == SCOPE_ALL_WAN:
        if scope_id is not None:
            raise PolicyValidationError(
                "scope='all_wan' already applies to every WAN link and must not "
                "carry a scope_id",
                field="scope_id",
            )
        return value
    if scope_id is None:
        raise PolicyValidationError(
            f"scope={value!r} requires a scope_id "
            f"({'a site id' if value == SCOPE_SITE else 'a topology link id'})",
            field="scope_id",
        )
    return value


def _coerce_threshold(field: str, value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise PolicyValidationError(f"{field} must be a number", field=field) from None
    if not math.isfinite(number):
        raise PolicyValidationError(f"{field} must be a finite number", field=field)
    if number < 0:
        raise PolicyValidationError(f"{field} cannot be negative", field=field)
    return number


def validate_attributes(attributes: Any) -> dict:
    """Free-form overflow must stay a JSON object; never a scalar or list."""
    if attributes is None:
        return {}
    if not isinstance(attributes, dict):
        raise PolicyValidationError(
            "attributes must be a JSON object", field="attributes"
        )
    return dict(attributes)


def validate_thresholds(
    *,
    metric: str,
    warning_threshold: Any,
    critical_threshold: Any,
    clear_threshold: Any,
) -> dict[str, float | None]:
    """Ordering and bounds check that honours the metric's direction.

    Higher-is-worse metrics breach when ``value >= level`` (latency, jitter,
    packet loss, utilisation); availability breaches when ``value <= level``.
    """
    warning = _coerce_threshold("warning_threshold", warning_threshold)
    critical = _coerce_threshold("critical_threshold", critical_threshold)
    clear = _coerce_threshold("clear_threshold", clear_threshold)

    if warning is None and critical is None:
        raise PolicyValidationError(
            "a policy needs at least one of warning_threshold or "
            "critical_threshold: with no level it can never breach, so it would "
            "look like coverage while providing none",
            field="critical_threshold",
        )

    lower_is_worse = metric in LOWER_IS_WORSE

    for field, value in (
        ("warning_threshold", warning),
        ("critical_threshold", critical),
        ("clear_threshold", clear),
    ):
        if value is None:
            continue
        if metric in PERCENT_METRICS and value > 100:
            raise PolicyValidationError(
                f"{metric} is a percentage, so {field} must be between 0 and 100; "
                f"got {value}",
                field=field,
            )
        if metric in LATENCY_METRICS and value > 86_400_000:
            raise PolicyValidationError(
                f"{field} is implausibly large for {metric} (milliseconds beyond "
                "a day); check the unit",
                field=field,
            )

    if warning is not None and critical is not None:
        if warning == critical:
            raise PolicyValidationError(
                "warning_threshold and critical_threshold must differ: with equal "
                "levels the warning could never be reported",
                field="critical_threshold",
            )
        if lower_is_worse and critical > warning:
            raise PolicyValidationError(
                f"for {metric}, lower values are worse, so critical_threshold must "
                f"be BELOW warning_threshold; got critical={critical} > "
                f"warning={warning}",
                field="critical_threshold",
            )
        if not lower_is_worse and critical < warning:
            raise PolicyValidationError(
                f"for {metric}, higher values are worse, so critical_threshold must "
                f"be ABOVE warning_threshold; got critical={critical} < "
                f"warning={warning}",
                field="critical_threshold",
            )

    entry = warning if warning is not None else critical
    if clear is not None and entry is not None:
        # Recovery must be EASIER than breach, otherwise hysteresis latches.
        if lower_is_worse and clear < entry:
            raise PolicyValidationError(
                f"for {metric}, clear_threshold must be at or above the entry "
                f"level ({entry}); got {clear}. A harder-to-reach clear level "
                "would never clear and the detector would latch.",
                field="clear_threshold",
            )
        if not lower_is_worse and clear > entry:
            raise PolicyValidationError(
                f"for {metric}, clear_threshold must be at or below the entry "
                f"level ({entry}); got {clear}. A harder-to-reach clear level "
                "would never clear and the detector would latch.",
                field="clear_threshold",
            )

    return {
        "warning_threshold": warning,
        "critical_threshold": critical,
        "clear_threshold": clear,
    }


#: Fields a PATCH may set. Anything else is rejected rather than ignored.
UPDATABLE_FIELDS: frozenset[str] = frozenset(
    {
        "name",
        "description",
        "scope",
        "scope_id",
        "metric",
        "warning_threshold",
        "critical_threshold",
        "clear_threshold",
        "min_consecutive_samples",
        "is_active",
        "attributes",
    }
)


class PolicyAdminService:
    """Tenant-scoped administration of the operational SLA policy store.

    Every read and write goes through the repository's tenant-scoped queries, so
    a policy belonging to another tenant is indistinguishable from a missing one.
    The service never commits: the caller owns the transaction (same convention
    as every other service in this codebase).
    """

    def __init__(self, db, *, publish: bool = True) -> None:
        self.db = db
        self.repo = OperationalSLAPolicyRepository(db)
        self.publish = publish

    # -- reads -------------------------------------------------------------
    async def list_policies(
        self,
        tenant_id: uuid.UUID,
        *,
        scope: str | None = None,
        metric: str | None = None,
        is_active: bool | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[OperationalSLAPolicy]:
        return await self.repo.list(
            tenant_id,
            scope=scope,
            metric=metric,
            is_active=is_active,
            limit=limit,
            offset=offset,
        )

    async def get(
        self, tenant_id: uuid.UUID, policy_id: uuid.UUID
    ) -> OperationalSLAPolicy | None:
        return await self.repo.get(policy_id, tenant_id)

    # -- writes ------------------------------------------------------------
    async def create(
        self,
        tenant_id: uuid.UUID,
        *,
        name: str,
        metric: str,
        scope: str = SCOPE_ALL_WAN,
        scope_id: uuid.UUID | None = None,
        description: str | None = None,
        warning_threshold: Any = None,
        critical_threshold: Any = None,
        clear_threshold: Any = None,
        min_consecutive_samples: Any = 2,
        is_active: bool = True,
        attributes: Any = None,
    ) -> OperationalSLAPolicy:
        """Validate and persist one policy, or raise without writing anything."""
        cleaned_name = normalise_name(name)
        cleaned_metric = validate_metric(metric)
        cleaned_scope = validate_scope(scope=scope, scope_id=scope_id)
        levels = validate_thresholds(
            metric=cleaned_metric,
            warning_threshold=warning_threshold,
            critical_threshold=critical_threshold,
            clear_threshold=clear_threshold,
        )
        samples = validate_min_samples(min_consecutive_samples)
        overflow = validate_attributes(attributes)

        if cleaned_scope != SCOPE_ALL_WAN:
            await self._assert_scope_exists(tenant_id, cleaned_scope, scope_id)
        await self._assert_name_available(tenant_id, cleaned_name)
        await self._assert_no_overlap(
            tenant_id,
            scope=cleaned_scope,
            scope_id=scope_id,
            metric=cleaned_metric,
        )

        row = await self.repo.create(
            tenant_id,
            name=cleaned_name,
            metric=cleaned_metric,
            scope=cleaned_scope,
            scope_id=scope_id,
            description=description,
            warning_threshold=levels["warning_threshold"],
            critical_threshold=levels["critical_threshold"],
            clear_threshold=levels["clear_threshold"],
            min_consecutive_samples=samples,
            is_active=bool(is_active),
            attributes=overflow,
        )
        await self._publish(
            "wan_sla_policy_created", tenant_id, row, policy_id=row.id
        )
        return row

    async def update(
        self, policy: OperationalSLAPolicy, **changes: Any
    ) -> OperationalSLAPolicy:
        """Partial update. Merged-then-validated, never field-by-field.

        A PATCH that only moves ``critical_threshold`` must still be checked
        against the STORED warning level, otherwise the pair could be left
        contradictory. Values are validated as the merged whole, then only the
        supplied keys are written.
        """
        unknown = sorted(set(changes) - UPDATABLE_FIELDS)
        if unknown:
            raise PolicyValidationError(
                f"cannot update {unknown}; allowed fields are "
                f"{sorted(UPDATABLE_FIELDS)}",
                field=unknown[0],
            )
        if not changes:
            raise PolicyValidationError(
                "no updatable field was supplied", field="policy"
            )

        merged = {
            "name": changes.get("name", policy.name),
            "description": changes.get("description", policy.description),
            "scope": changes.get("scope", policy.scope),
            "scope_id": changes.get("scope_id", policy.scope_id),
            "metric": changes.get("metric", policy.metric),
            "warning_threshold": changes.get(
                "warning_threshold", policy.warning_threshold
            ),
            "critical_threshold": changes.get(
                "critical_threshold", policy.critical_threshold
            ),
            "clear_threshold": changes.get("clear_threshold", policy.clear_threshold),
            "min_consecutive_samples": changes.get(
                "min_consecutive_samples", policy.min_consecutive_samples
            ),
            "is_active": changes.get("is_active", policy.is_active),
            "attributes": changes.get("attributes", policy.attributes),
        }

        merged["name"] = normalise_name(merged["name"])
        merged["metric"] = validate_metric(merged["metric"])
        merged["scope"] = validate_scope(
            scope=merged["scope"], scope_id=merged["scope_id"]
        )
        merged.update(
            validate_thresholds(
                metric=merged["metric"],
                warning_threshold=merged["warning_threshold"],
                critical_threshold=merged["critical_threshold"],
                clear_threshold=merged["clear_threshold"],
            )
        )
        merged["min_consecutive_samples"] = validate_min_samples(
            merged["min_consecutive_samples"]
        )
        merged["attributes"] = validate_attributes(merged["attributes"])
        merged["is_active"] = bool(merged["is_active"])

        # Targeted checks run only when the thing they guard actually changed, so
        # editing a description can never be blocked by unrelated state.
        if {"scope", "scope_id"} & set(changes) and merged["scope"] != SCOPE_ALL_WAN:
            await self._assert_scope_exists(
                policy.tenant_id, merged["scope"], merged["scope_id"]
            )
        if "name" in changes:
            await self._assert_name_available(
                policy.tenant_id, merged["name"], exclude_id=policy.id
            )
        if {"scope", "scope_id", "metric"} & set(changes):
            await self._assert_no_overlap(
                policy.tenant_id,
                scope=merged["scope"],
                scope_id=merged["scope_id"],
                metric=merged["metric"],
                exclude_id=policy.id,
            )

        applied = {key: merged[key] for key in changes}
        row = await self.repo.update(policy, **applied)
        await self._publish(
            "wan_sla_policy_updated", policy.tenant_id, row, policy_id=row.id
        )
        return row

    async def delete(self, policy: OperationalSLAPolicy) -> OperationalSLAPolicy:
        """Soft delete. The row is retained (never destroyed) as an audit record."""
        await self._publish(
            "wan_sla_policy_deleted", policy.tenant_id, policy, policy_id=policy.id
        )
        await self.repo.delete(policy)
        return policy

    # -- guards ------------------------------------------------------------
    async def _assert_scope_exists(
        self, tenant_id: uuid.UUID, scope: str, scope_id: uuid.UUID | None
    ) -> None:
        """The scope target must exist IN THIS TENANT, or the policy is a no-op.

        A foreign tenant's id fails here exactly like an unknown one, so nothing
        about another tenant is disclosed.
        """
        from app.repositories.site_repository import SiteRepository
        from app.repositories.topology_link_repository import TopologyLinkRepository

        if scope == SCOPE_SITE:
            found = await SiteRepository(self.db).get(scope_id, tenant_id)
            label = "site"
        else:
            found = await TopologyLinkRepository(self.db).get(scope_id, tenant_id)
            label = "topology link"
        if found is None:
            raise PolicyValidationError(
                f"{label} {scope_id} does not exist in this tenant; a policy "
                "pointing at it would silently match no WAN link",
                field="scope_id",
            )

    async def _assert_name_available(
        self, tenant_id: uuid.UUID, name: str, *, exclude_id: uuid.UUID | None = None
    ) -> None:
        wanted = name.casefold()
        for row in await self.repo.list(tenant_id, limit=500):
            if exclude_id is not None and row.id == exclude_id:
                continue
            if row.name.strip().casefold() == wanted:
                raise PolicyConflictError(
                    f"a policy named {row.name!r} already exists in this tenant",
                    field="name",
                )

    async def _assert_no_overlap(
        self,
        tenant_id: uuid.UUID,
        *,
        scope: str,
        scope_id: uuid.UUID | None,
        metric: str,
        exclude_id: uuid.UUID | None = None,
    ) -> None:
        """One policy per (scope, scope_id, metric).

        The resolver returns exactly ONE policy per metric for a link, so a
        second policy at the same scope would be dead config and would make the
        resolved answer depend on row order.
        """
        target = str(scope_id) if scope_id is not None else None
        for row in await self.repo.list(tenant_id, scope=scope, metric=metric):
            if exclude_id is not None and row.id == exclude_id:
                continue
            row_target = str(row.scope_id) if row.scope_id is not None else None
            if row_target != target:
                continue
            raise PolicyConflictError(
                f"policy {row.name!r} already covers metric={metric} at "
                f"scope={scope}"
                + (f" (scope_id={target})" if target else "")
                + "; edit that policy instead, or scope this one more specifically",
                field="metric",
            )

    async def _publish(
        self,
        event_type: str,
        tenant_id: uuid.UUID,
        policy: OperationalSLAPolicy,
        *,
        policy_id: uuid.UUID,
    ) -> None:
        """Best-effort audit notification; never allowed to break the write."""
        if not self.publish:
            return
        try:
            from app.services.events.event_bus import (
                DomainEvent,
                EventType,
                get_event_bus,
            )

            await get_event_bus().publish(
                DomainEvent(
                    event_type=EventType(event_type),
                    tenant_id=tenant_id,
                    payload={
                        "policy_id": str(policy_id),
                        "name": policy.name,
                        "scope": policy.scope,
                        "scope_id": str(policy.scope_id) if policy.scope_id else None,
                        "metric": policy.metric,
                        "is_active": policy.is_active,
                        "warning_threshold": policy.warning_threshold,
                        "critical_threshold": policy.critical_threshold,
                        "clear_threshold": policy.clear_threshold,
                    },
                )
            )
        except Exception:  # noqa: BLE001 - notification is not the product
            logger.debug("SLA policy event publish failed", exc_info=True)

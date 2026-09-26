"""Shared scaffolding for vendor-specific SNMP health providers (Phase 3).

WHY THIS EXISTS: Cisco and Aruba both expose CPU/memory counters that MIB-II
does not, but the *plumbing* around them is identical - resolve the device's
already-decrypted monitoring source, drive the SNMPCollector transport, time
the call, and honour the honest ActionResult contract (a device-level
failure is `success=False`, never an exception and never a fabricated
reading).

That plumbing lives here ONCE. A vendor module supplies only:
  * `health_actions`  - the DeviceActionType(s) it implements
  * `read_health()`   - its OID map + reading parser

`execute()` additionally classifies every reading through
`app.actions.health_thresholds` (params["thresholds"] overrides the
defaults) so both vendors get warning/critical verdicts for free.

This mirrors the existing reuse decision in `providers/snmp.py`: there is
still exactly ONE SNMP transport implementation in the codebase
(app/collectors/snmp.py), and now exactly ONE vendor-SNMP envelope.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, ClassVar

from app.actions.base import (
    ActionNotSupported,
    ActionResult,
    ActionTarget,
    DeviceActionProvider,
    DeviceActionType,
)
from app.actions.health_thresholds import evaluate_health
from app.collectors.snmp import SNMPCollector

logger = logging.getLogger(__name__)


def to_int(value: Any) -> int | None:
    """Parse an SNMP value into an int, or None.

    pysnmp stringifies values; some agents return ``"42 "`` or ``"42\n"``.
    Returns None rather than guessing when the value is not numeric - a
    missing reading must stay missing, never become a silent 0.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(text)
    except (TypeError, ValueError):
        return None


def walk_column(
    oid_map: dict[str, str], rows: list[tuple[str, str]]
) -> dict[str, str]:
    """Map a walk result onto the last OID arc (the table row index).

    ``[(".1.3...13.1.3.1", "17")]`` -> ``{"1": "17"}`` so columns of the
    same table can be joined on the shared index.
    """
    return {oid.split(".")[-1]: val for oid, val in rows}


def walk_composite(
    rows: list[tuple[str, str]], arcs: int = 2
) -> dict[str, str]:
    """Map a walk result onto the last N OID arcs (composite table index).

    Tables whose INDEX has multiple components (e.g. ``{cpmCPUTotalIndex,
    cpmProcessPID}``) return OIDs whose last N arcs are the composite key;
    joining several columns on that key reconstructs each row.

    ``[(\".1.3...2.1.5.2.1234\", \"45\")]`` with ``arcs=2`` -> ``{\"2.1234\":
    \"45\"}``.
    """
    return {
        ".".join(oid_str.split(".")[-arcs:]): val for oid_str, val in rows
    }


class VendorSnmpHealthProvider(DeviceActionProvider):
    """Base class: shared connection resolution + execute() envelope.

    Subclasses MUST define `health_actions` and override `read_health()`.
    """

    health_actions: ClassVar[frozenset[DeviceActionType]] = frozenset()

    def supported_actions(self) -> frozenset[DeviceActionType]:
        return self.health_actions

    def _collector(self, target: ActionTarget) -> SNMPCollector:
        """The device's linked monitoring source supplies connection info
        (already decrypted by the capability resolver). Monitoring
        credentials are READ-ONLY by contract - this provider never writes."""
        if target.connection is None:
            raise ActionNotSupported(
                f"Device has no linked SNMP monitoring source for {self.provider_kind}."
            )
        return SNMPCollector(source=target.connection)

    async def read_health(
        self, collector: SNMPCollector, action: DeviceActionType
    ) -> dict[str, Any]:
        """Return the vendor health payload for the requested action.

        Subclasses override this and dispatch on ``action`` so the same
        provider can serve multiple DeviceActionTypes (e.g. totals vs
        per-process) without a second transport.
        """
        raise NotImplementedError

    async def execute(
        self,
        action: DeviceActionType,
        target: ActionTarget,
        params: dict[str, Any] | None = None,
    ) -> ActionResult:
        if not self.supports(action):
            raise ActionNotSupported(
                f"{self.provider_kind} does not support action={action.value}"
            )
        collector = self._collector(target)
        started = datetime.now(timezone.utc)
        try:
            output = await self.read_health(collector, action)
        except ActionNotSupported:
            raise
        except Exception as exc:  # noqa: BLE001 - device-level failure
            return ActionResult(
                success=False,
                error=f"{self.display_name} SNMP query failed: {type(exc).__name__}",
                provider=self.provider_kind,
                started_at=started,
                completed_at=datetime.now(timezone.utc),
            )

        ok = bool(output)
        if ok:
            # Threshold classification rides the shared envelope so BOTH
            # vendors get it once. Overrides come from the request params;
            # a malformed override falls back to the defaults rather than
            # failing an otherwise-successful read.
            try:
                output["threshold_evaluation"] = evaluate_health(
                    output, (params or {}).get("thresholds")
                )
            except Exception:  # noqa: BLE001 - never fail a good read
                logger.warning(
                    "threshold evaluation failed for %s; reporting without it",
                    self.provider_kind, exc_info=True,
                )
        return ActionResult(
            success=ok,
            output=output,
            error=None if ok else (
                f"{self.display_name}: no vendor health data returned "
                "(unreachable, wrong community, or vendor MIB not supported "
                "by this device)."
            ),
            provider=self.provider_kind,
            started_at=started,
            completed_at=datetime.now(timezone.utc),
        )

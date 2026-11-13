"""SOP-016 (internet down, layered triage): seeds as an approved SOP and is
recommended for internet-outage incidents."""
from __future__ import annotations

import copy

import pytest
from sqlalchemy import select

from app.models.sop import SOP, SOPVersion
from app.services.alerting import event_classifier
from app.services.sop.sop_matcher import IncidentContext, SOPMatcher
from scripts import seed_sops

pytestmark = pytest.mark.asyncio


def _seed_016() -> dict:
    return copy.deepcopy(next(s for s in seed_sops.SEEDS if s["sop_number"] == "SOP-016"))


def test_internet_outage_alerts_are_connectivity():
    assert event_classifier.classify(title="Internet down at Head Office") == "connectivity"
    assert event_classifier.classify(title="No internet - site WAN unreachable") == "connectivity"
    # A plain reachability alert stays availability.
    assert event_classifier.classify(metric_type="ping", title="Core switch down") == "availability"


def test_sop_016_content_is_complete():
    sop = _seed_016()
    assert sop["category"] == "connectivity" and not sop["requires_approval"]
    orders = [s["order"] for s in sop["steps"]]
    assert orders == list(range(1, len(orders) + 1)) and len(orders) == 9
    for step in sop["steps"]:
        assert step["action"] and step["expected_result"] and step["troubleshooting_matrix"], step["order"]
    platforms = {c["platform"] for s in sop["steps"] for c in s.get("commands", [])}
    assert {"windows", "linux", "cisco_ios", "fortios", "meraki"} <= platforms
    # Hands over to the specific internet-outage SOPs when the evidence points there.
    text = " ".join(str(v) for s in sop["steps"] for v in s["troubleshooting_matrix"].values())
    assert "SOP-006" in text and "SOP-009" in text


async def test_seeds_as_an_approved_sop_and_matches_internet_incidents(db, tenant, monkeypatch):
    monkeypatch.setattr(seed_sops, "SEEDS", [_seed_016()])
    assert await seed_sops.seed_sops(db, tenant.id) == 1
    await db.commit()
    monkeypatch.setattr(seed_sops, "SEEDS", [_seed_016()])
    assert await seed_sops.seed_sops(db, tenant.id) == 0  # idempotent

    sop = (await db.execute(select(SOP).where(SOP.tenant_id == tenant.id, SOP.sop_number == "SOP-016"))).scalar_one()
    version = await db.get(SOPVersion, sop.current_version_id)
    assert version.status == "approved" and len(version.steps) == 9

    category = event_classifier.classify(title="Internet down at Head Office")
    match = SOPMatcher().score(sop, IncidentContext(category=category, vendor="Fortinet", device_type="firewall"))
    assert match is not None and match.confidence_score > 0
    assert SOPMatcher().score(sop, IncidentContext(category="capacity")) is None

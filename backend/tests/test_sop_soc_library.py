"""SOC playbooks SOP-017..022: complete, safe-by-default content that seeds as
approved SOPs and matches the incidents it is written for."""
from __future__ import annotations

import copy

import pytest
from sqlalchemy import select

from app.models.sop import SOP, SOPVersion
from app.services.sop.sop_matcher import IncidentContext, SOPMatcher
from scripts import seed_sops
from scripts.sop_soc_library import SOC_SEEDS

NUMBERS = ["SOP-017", "SOP-018", "SOP-019", "SOP-020", "SOP-021", "SOP-022"]


def _by_number(n: str) -> dict:
    return copy.deepcopy(next(s for s in SOC_SEEDS if s["sop_number"] == n))


def test_soc_library_is_registered_with_the_seeder():
    assert [s["sop_number"] for s in SOC_SEEDS] == NUMBERS
    numbers = [s["sop_number"] for s in seed_sops.SEEDS]
    assert len(numbers) == len(set(numbers))  # no duplicate SOP numbers
    assert set(NUMBERS) <= set(numbers)


@pytest.mark.parametrize("number", NUMBERS)
def test_every_soc_sop_is_complete(number):
    sop = _by_number(number)
    assert "soc" in sop["tags"] and sop["name"] and sop["description"] and sop["rollback_procedure"]
    orders = [s["order"] for s in sop["steps"]]
    assert orders == list(range(1, len(orders) + 1)) and len(orders) >= 4
    for step in sop["steps"]:
        assert step["action"] and step["expected_result"], (number, step["order"])
        for cmd in step.get("commands", []):
            assert cmd["platform"] and cmd["command"], (number, step["order"])
    # Every step except the closing one says what each failure means.
    assert all(s["troubleshooting_matrix"] for s in sop["steps"][:-1]), number


def test_firewall_diagnostic_covers_the_top_vendors():
    sop = _by_number("SOP-017")
    assert sop["category"] == "connectivity" and sop["device_type"] == "firewall" and not sop.get("vendor")
    platforms = {c["platform"] for s in sop["steps"] for c in s.get("commands", [])}
    assert {"fortios", "panos", "cisco_asa", "cisco_ftd", "checkpoint", "junos_srx", "sonicwall", "sophos"} <= platforms
    fortios = " ".join(c["command"] for s in sop["steps"] for c in s.get("commands", []) if c["platform"] == "fortios")
    assert "iprope lookup" in fortios and "diagnose sniffer packet" in fortios


def test_security_playbooks_need_approval():
    for n in ["SOP-018", "SOP-019", "SOP-020", "SOP-021"]:
        sop = _by_number(n)
        assert sop["category"] == "security" and sop["requires_approval"], n


def test_fortigate_scenarios_cover_the_quick_book():
    sop = _by_number("SOP-022")
    assert (sop["vendor"], sop["platform"], sop["device_type"]) == ("Fortinet", "fortios", "firewall")
    actions = " ".join(s["action"] for s in sop["steps"]).lower()
    for phrase in ["ping the gateway", "website is blocked", "vpn is up", "policy change", "ha failover",
                   "vip", "web filtering", "conserve mode"]:
        assert phrase in actions, phrase
    # Disruptive commands are flagged, never presented as routine.
    disruptive = [c for s in sop["steps"] for c in s["commands"] if c.get("disruptive")]
    assert disruptive and all("approval" in c["command"] for c in disruptive)


@pytest.mark.asyncio
async def test_soc_sops_seed_and_match(db, tenant, monkeypatch):
    monkeypatch.setattr(seed_sops, "SEEDS", copy.deepcopy(SOC_SEEDS))
    assert await seed_sops.seed_sops(db, tenant.id) == len(SOC_SEEDS)
    await db.commit()
    monkeypatch.setattr(seed_sops, "SEEDS", copy.deepcopy(SOC_SEEDS))
    assert await seed_sops.seed_sops(db, tenant.id) == 0  # idempotent

    rows = {s.sop_number: s for s in (await db.execute(select(SOP).where(SOP.tenant_id == tenant.id))).scalars()}
    assert set(NUMBERS) <= set(rows)
    for n in NUMBERS:
        version = await db.get(SOPVersion, rows[n].current_version_id)
        assert version.status == "approved" and version.steps, n

    m = SOPMatcher()
    fw_incident = IncidentContext(category="connectivity", vendor="Fortinet", platform="fortios", device_type="firewall")
    generic, forti = m.score(rows["SOP-017"], fw_incident), m.score(rows["SOP-022"], fw_incident)
    assert generic is not None and forti is not None
    assert forti.confidence_score >= generic.confidence_score  # vendor-specific ranks at least as high
    assert m.score(rows["SOP-022"], IncidentContext(category="connectivity", vendor="Palo Alto Networks")) is None \
        or m.score(rows["SOP-022"], IncidentContext(category="connectivity", vendor="Palo Alto Networks")).confidence_score \
        < forti.confidence_score

    ips = IncidentContext(category="security", device_type="firewall")
    assert m.score(rows["SOP-018"], ips) is not None
    assert m.score(rows["SOP-018"], IncidentContext(category="capacity")) is None

"""SASE policy intelligence tests (HSDEEP network intelligence).

Pure computation layer: deterministic evaluation of one flow through the
Identity -> Device Posture -> ZTNA -> SWG -> CASB -> FWaaS pipeline.
"""
from __future__ import annotations

from app.services.network_intelligence.sase import evaluate_sase

SAFE_FLOW = {
    "user_identity": "employee@company.com",
    "device_identity": "Windows-Laptop-01",
    "device_compliance": True,
    "destination": "intranet.company.com",
    "url_category": "business",
    "application": "HTTPS",
    "risk_level": "low",
}


class TestSasePolicy:
    def test_compliant_safe_destination_allows(self):
        result = evaluate_sase(SAFE_FLOW)
        assert result["decision"] == "ALLOW"
        assert result["pipeline"][-1]["stage"] == "Final Decision"

    def test_non_compliant_device_denies(self):
        result = evaluate_sase({**SAFE_FLOW, "device_compliance": False})
        assert result["decision"] == "BLOCK"
        ztna = next(s for s in result["pipeline"] if s["stage"] == "ZTNA")
        assert ztna["decision"] in ("DENY", "NOT_EVALUATED")
        assert "compliant" in result["reason"].lower()

    def test_malicious_destination_blocks(self):
        result = evaluate_sase({
            **SAFE_FLOW,
            "destination": "malicious-site.example",
            "url_category": "malware",
        })
        assert result["decision"] == "BLOCK"
        swg = next(s for s in result["pipeline"] if s["stage"] == "SWG")
        assert swg["decision"] in ("BLOCK", "NOT_EVALUATED")

    def test_application_policy_violation_blocks(self):
        result = evaluate_sase({**SAFE_FLOW, "application": "tor"})
        assert result["decision"] == "BLOCK"
        fwaas = next(s for s in result["pipeline"] if s["stage"] == "FWaaS")
        assert fwaas["decision"] == "BLOCK"

    def test_valid_identity_compliant_allowed_app_allows(self):
        result = evaluate_sase({
            **SAFE_FLOW,
            "user_identity": "analyst@company.com",
            "application": "https",
        })
        assert result["decision"] == "ALLOW"

    def test_combined_noncompliant_malicious_blocks(self):
        result = evaluate_sase({
            "user_identity": "employee@company.com",
            "device_identity": "Windows-Laptop-01",
            "device_compliance": False,
            "destination": "malicious-site.example",
            "url_category": "malware",
            "application": "HTTPS",
            "risk_level": "high",
        })
        assert result["decision"] == "BLOCK"
        assert result["reason"] == (
            "Non-compliant device attempting access to malicious destination"
        )

    def test_pipeline_shape_is_stable(self):
        result = evaluate_sase(SAFE_FLOW)
        stages = [s["stage"] for s in result["pipeline"]]
        assert stages == [
            "Identity", "Device Posture", "ZTNA", "SWG",
            "CASB", "FWaaS", "Final Decision",
        ]

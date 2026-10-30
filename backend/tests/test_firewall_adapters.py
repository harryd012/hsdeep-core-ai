"""Firewall gateway adapters: FortiGate and PAN-OS normalization (no I/O)."""
from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from app.collectors.fortigate import _FIREWALL_READS, FortiGateCollector
from app.collectors.paloalto import PaloAltoCollector, parse_ha, parse_licences, parse_resources

# ---------------------------------------------------------------------------
# FortiGate
# ---------------------------------------------------------------------------


def _forti_raw(**overrides):
    raw = {
        "status": {"results": {"hostname": "fgt-edge", "model": "FGT60F"}, "version": "v7.4.3", "serial": "FGT-A"},
        "interfaces": {"results": []},
        "fw": {
            "ha": {"results": {"mode": "a-p"}},
            "ha_checksums": {"results": [
                {"serial_no": "FGT-A", "checksum": {"all": "abc"}},
                {"serial_no": "FGT-B", "checksum": {"all": "abc"}},
            ]},
            "resources": {"results": {
                "cpu": [{"interval": "1-min", "current": 12}],
                "mem": [{"interval": "1-min", "current": 61}],
                "session": [{"interval": "1-min", "current": 4200}],
            }},
            "vpn": {"results": [
                {"name": "to-branch", "rgwy": "198.51.100.7", "incoming_bytes": 10, "outgoing_bytes": 20,
                 "proxyid": [{"status": "up"}, {"status": "down"}]},
                {"name": "dialup", "rgwy": "0.0.0.0", "proxyid": []},
            ]},
            "licences": {"results": {
                "antivirus": {"status": "licensed", "expires": 1893456000},
                "web_filtering": {"status": "expired", "expires": 1600000000},
                "vdom": {"max": 10},
            }},
        },
        "fw_status": {k: "collected" for k, _, _ in _FIREWALL_READS},
    }
    raw.update(overrides)
    return raw


def _forti():
    return FortiGateCollector(source=SimpleNamespace(id="s", hostname="fgt", password="x", api_key=None, protocol="https", port=None, verify_ssl=True))


def test_fortigate_status_tunnels_and_licences():
    bundle = _forti().normalize(_forti_raw())
    [fw] = bundle.firewalls
    assert (fw.ha_mode, fw.ha_sync, fw.ha_peer_serial) == ("active-passive", "in_sync", "FGT-B")
    assert (fw.cpu_percent, fw.memory_percent, fw.session_count, fw.session_limit) == (12.0, 61.0, 4200, None)
    tunnels = {t.name: t for t in bundle.vpn_tunnels}
    assert (tunnels["to-branch"].status, tunnels["to-branch"].phase2_up, tunnels["to-branch"].phase2_total) == ("up", 1, 2)
    assert tunnels["dialup"].status == "down"
    lics = {lic.feature: lic for lic in bundle.licences}
    assert set(lics) == {"antivirus", "web_filtering"}
    assert lics["web_filtering"].expired is True and lics["antivirus"].expires_at.year == 2030
    assert bundle.firewall_status == {"status": "collected", "vpn": "collected", "licences": "collected"}


def test_fortigate_lost_peer_is_out_of_sync():
    raw = _forti_raw()
    raw["fw"]["ha_checksums"] = {"results": [{"serial_no": "FGT-A", "checksum": {"all": "abc"}}]}
    assert _forti().normalize(raw).firewalls[0].ha_sync == "out_of_sync"


def test_fortigate_unreadable_tables_are_not_empty():
    raw = _forti_raw(fw={}, fw_status={"ha": "unavailable", "ha_checksums": "unavailable",
                                       "resources": "failed", "vpn": "unavailable", "licences": "failed"})
    bundle = _forti().normalize(raw)
    assert bundle.firewalls == [] and bundle.vpn_tunnels == []
    assert bundle.firewall_status == {"status": "failed", "vpn": "unavailable", "licences": "failed"}


def test_fortigate_without_firewall_reads_is_unchanged():
    raw = _forti_raw()
    del raw["fw"], raw["fw_status"]
    assert _forti().normalize(raw).firewall_status == {}


@pytest.mark.asyncio
async def test_fortigate_collect_only_issues_gets(monkeypatch):
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        return httpx.Response(200, json={"results": {}})

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    raw = await _forti().collect()
    assert set(methods) == {"GET"} and len(methods) == 3 + len(_FIREWALL_READS)
    assert raw["fw_status"] == {k: "collected" for k, _, _ in _FIREWALL_READS}


# ---------------------------------------------------------------------------
# PAN-OS
# ---------------------------------------------------------------------------

HA = """<response status="success"><result><enabled>yes</enabled><group>
<mode>Active-Passive</mode><local-info><state>active</state></local-info>
<peer-info><state>passive</state><serial-num>0123B</serial-num><conn-status>up</conn-status></peer-info>
<running-sync>synchronized</running-sync></group></result></response>"""

RESOURCES = """<response status="success"><result><![CDATA[top - 10:00:00 up 3 days
%Cpu(s):  7.0 us,  2.0 sy,  0.0 ni, 90.0 id,  1.0 wa
MiB Mem :   7948.0 total,    300.0 free,   4000.0 used,   3648.0 buff/cache
MiB Swap:      0.0 total,      0.0 free,      0.0 used.   3900.0 avail Mem
]]></result></response>"""

SESSIONS = """<response status="success"><result><num-max>262142</num-max><num-active>1500</num-active></result></response>"""

VPN = """<response status="success"><result><IPSec>
<entry><name>to-dc</name><state>active</state><peerip>192.0.2.10</peerip></entry>
<entry><name>to-lab</name><state>init</state><peerip>192.0.2.11</peerip></entry>
</IPSec></result></response>"""

LICENCES = """<response status="success"><result><licenses>
<entry><feature>Threat Prevention</feature><expires>November 26, 2026</expires><expired>no</expired></entry>
<entry><feature>PA-VM</feature><expires>Never</expires><expired>no</expired></entry>
</licenses></result></response>"""


def _pan_raw(**overrides):
    raw = {
        "system": "<response><result><system><hostname>pa-1</hostname><serial-number>0123A</serial-number>"
                  "<sw-version>11.1.2</sw-version></system></result></response>",
        "interface": "", "routing": "",
        "highavailability": HA, "sessions": SESSIONS, "resources": RESOURCES, "vpn": VPN, "licences": LICENCES,
    }
    raw.update({f"{k}_status": "collected" for k in ("system", "highavailability", "sessions", "resources", "vpn", "licences")})
    raw.update(overrides)
    return raw


def _pan():
    return PaloAltoCollector(source=SimpleNamespace(id="s", hostname="pa", password="x", api_key=None, ip_address=None, protocol="https", port=None, verify_ssl=True, username="u"))


def test_panos_status_tunnels_and_licences():
    bundle = _pan().normalize(_pan_raw())
    [fw] = bundle.firewalls
    assert fw.device_external_id == "0123A"
    assert (fw.ha_mode, fw.ha_role, fw.ha_sync, fw.ha_peer_serial) == ("active-passive", "active", "in_sync", "0123B")
    assert (fw.session_count, fw.session_limit, fw.firmware_version) == (1500, 262142, "11.1.2")
    assert fw.cpu_percent == 10.0 and fw.memory_percent == 50.9  # (total - avail) / total
    assert {(t.name, t.status, t.remote_gateway) for t in bundle.vpn_tunnels} == {
        ("to-dc", "up", "192.0.2.10"), ("to-lab", "down", "192.0.2.11"),
    }
    lics = {lic.feature: lic for lic in bundle.licences}
    assert lics["Threat Prevention"].expires_at.isoformat().startswith("2026-11-26") and lics["PA-VM"].expires_at is None
    assert bundle.firewall_status == {"status": "collected", "vpn": "collected", "licences": "collected"}


def test_panos_ha_variants():
    assert parse_ha("<response><result><enabled>no</enabled></result></response>") == {"ha_mode": "standalone"}
    lost = HA.replace("<conn-status>up</conn-status>", "<conn-status>down</conn-status>")
    assert parse_ha(lost)["ha_sync"] == "out_of_sync"
    assert parse_ha("garbage") == {}


def test_panos_error_answers_are_unavailable_not_empty():
    bundle = _pan().normalize(_pan_raw(vpn="", vpn_status="unavailable", licences="", licences_status="failed"))
    assert bundle.firewall_status["vpn"] == "unavailable" and bundle.firewall_status["licences"] == "failed"
    assert bundle.vpn_tunnels == []


def test_panos_parsers_never_invent_values():
    assert parse_resources("") == (None, None)
    assert parse_licences("<x/>", "pa") == []


@pytest.mark.asyncio
async def test_panos_collect_issues_only_show_and_licence_info(monkeypatch):
    bodies: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(request.content.decode())
        return httpx.Response(200, text='<response status="error"><msg>no</msg></response>')

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    raw = await _pan().collect()
    assert all(b.startswith("<request><cmd><show>") or "<license><info/>" in b for b in bodies)
    assert not any(word in b for b in bodies for word in ("<commit", "<set>", "<delete", "restart", "reboot"))
    assert raw["vpn_status"] == "unavailable"

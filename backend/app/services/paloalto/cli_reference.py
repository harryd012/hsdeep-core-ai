"""Palo Alto CLI diagnostic reference (read-only, version-aware)."""
from __future__ import annotations
from app.schemas.paloalto import CLIDiagClass, CLIDiagnosticCommand


def _cmd(**kw) -> CLIDiagnosticCommand:
    return CLIDiagnosticCommand(**kw)


COMMANDS: list[CLIDiagnosticCommand] = [
    _cmd(id="sys_info", category="system", command_template="show system info",
         description="Hostname, serial, model, PAN-OS version, uptime.",
         min_panos_version="10.0", max_panos_version=None, diag_class=CLIDiagClass.BACKEND_SAFE),
    _cmd(id="int_all", category="interface", command_template="show interface all",
         description="Admin/oper state of all interfaces.",
         min_panos_version="10.0", max_panos_version=None, diag_class=CLIDiagClass.BACKEND_SAFE),
    _cmd(id="int_hw", category="interface", command_template="show interface hardware",
         description="Interface counters, errors, drops.",
         min_panos_version="10.0", max_panos_version=None, diag_class=CLIDiagClass.BACKEND_SAFE),
    _cmd(id="route_table", category="routing", command_template="show routing route",
         description="Active routing table and next hops.",
         min_panos_version="10.0", max_panos_version=None, diag_class=CLIDiagClass.BACKEND_SAFE),
    _cmd(id="sec_policy", category="security", command_template="show running security-policy",
         description="Running security policy (post-commit).",
         min_panos_version="10.0", max_panos_version=None, diag_class=CLIDiagClass.BACKEND_SAFE),
    _cmd(id="sec_hit", category="security", command_template="show rule-hit-count security",
         description="Per-rule hit counts for rule-order triage.",
         min_panos_version="10.0", max_panos_version=None, diag_class=CLIDiagClass.BACKEND_SAFE),
    _cmd(id="nat_policy", category="nat", command_template="show running nat-policy",
         description="Running NAT policy.",
         min_panos_version="10.0", max_panos_version=None, diag_class=CLIDiagClass.BACKEND_SAFE),
    _cmd(id="sess_flow", category="session", command_template="show session all filter source {src} destination {dst}",
         description="Live sessions matching the flow (bounded output).",
         min_panos_version="10.0", max_panos_version=None, diag_class=CLIDiagClass.BACKEND_SAFE),
    _cmd(id="counters", category="counters", command_template="show counter global filter packet-filter yes",
         description="Global packet counters incl. drops.",
         min_panos_version="10.0", max_panos_version=None, diag_class=CLIDiagClass.BACKEND_SAFE),
    _cmd(id="ha_state", category="ha", command_template="show high-availability state",
         description="HA pair state and peer status.",
         min_panos_version="10.0", max_panos_version=None, diag_class=CLIDiagClass.BACKEND_SAFE),
    _cmd(id="gp_users", category="globalprotect", command_template="show global-protect-gateway current-user",
         description="Current GP users and tunnel state.",
         min_panos_version="10.0", max_panos_version=None, diag_class=CLIDiagClass.BACKEND_SAFE),
    _cmd(id="log_example", category="logs", command_template="show log traffic ... (prefer API/syslog)",
         description="Log query example; prefer API/syslog path.",
         min_panos_version="10.0", max_panos_version=None, diag_class=CLIDiagClass.DOC_ONLY),
]

BACKEND_SAFE_IDS = frozenset(
    c.id for c in COMMANDS if c.diag_class == CLIDiagClass.BACKEND_SAFE
)


class CLIReference:
    """Version-aware lookup over the approved catalogue."""

    def __init__(self, commands=None):
        self._commands = commands if commands is not None else list(COMMANDS)

    def all(self):
        return list(self._commands)

    def get(self, command_id):
        for c in self._commands:
            if c.id == command_id:
                return c
        return None

    def backend_safe(self, panos_version=None):
        out = [c for c in self._commands if c.diag_class == CLIDiagClass.BACKEND_SAFE]
        if panos_version is None:
            return out
        return [c for c in out if self._version_ok(c, panos_version)]

    @staticmethod
    def _version_ok(cmd, version):
        def tup(v):
            return tuple(int(x) for x in v.split(".") if x.isdigit())
        try:
            cur = tup(version)
        except ValueError:
            return True
        if cmd.min_panos_version and cur < tup(cmd.min_panos_version):
            return False
        if cmd.max_panos_version and cur > tup(cmd.max_panos_version):
            return False
        return True

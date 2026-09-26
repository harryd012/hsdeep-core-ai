"""Action provider package - providers self-register on import."""
from app.actions.providers import icmp as _icmp_provider  # noqa: F401
from app.actions.providers import snmp as _snmp_provider  # noqa: F401
from app.actions.providers import prtg as _prtg_provider  # noqa: F401
from app.actions.providers import cisco as _cisco_provider  # noqa: F401
from app.actions.providers import aruba as _aruba_provider  # noqa: F401

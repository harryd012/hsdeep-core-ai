"""
Monitoring Engine service package.

Importing this package registers every built-in collector (via the
`@CollectorRegistry.register` decorators executing at import time), so
anything that needs the registry populated just needs to import
`app.services.monitoring` once at startup. Collector registration now
lives under `app/collectors/` (the Collector Framework's evolution of the
old `app/services/monitoring/adapters/` package, retired in task 10.3).
"""
from app.collectors import base  # noqa: F401  (registration side effects)
from app.collectors import (  # noqa: F401  (registration side effects)
    aws,
    azure,
    connectivity,
    fortigate,
    fortimanager,
    gcp,
    kubernetes,
    linux_ssh,
    meraki,
    pnetlab,
    prtg,
    snmp,
    vmware,
    windows_winrm,
    wmi,
    zabbix,
)

#: Every collector module that must be imported for its registration to run.
#: Kept as data so `tests/test_collector_registration.py` can assert this set
#: matches the `@CollectorRegistry.register` modules found in `app/collectors/`.
#: This used to list only `base, prtg, connectivity`, which left 8 complete
#: collectors (azure, vmware, fortigate, fortimanager, meraki, zabbix,
#: linux_ssh, windows_winrm) unreachable at runtime.
_COLLECTOR_MODULES: tuple[str, ...] = (
    "aws",
    "azure",
    "connectivity",
    "fortigate",
    "fortimanager",
    "gcp",
    "kubernetes",
    "linux_ssh",
    "meraki",
    "pnetlab",
    "prtg",
    "snmp",
    "vmware",
    "windows_winrm",
    "wmi",
    "zabbix",
)

__all__: list[str] = []

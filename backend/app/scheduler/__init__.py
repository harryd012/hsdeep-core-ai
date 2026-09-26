"""
Scheduler package: fires `CollectionOrchestrator.run()` for every enabled,
due `MonitoringSource` once a minute, safely under multiple workers.

See design.md's "Component: Scheduler (`app/scheduler/`)" section and its
`tick()`/`run_bounded()` pseudocode for the authoritative spec this package
implements.
"""

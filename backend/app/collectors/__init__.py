"""Collector Framework: vendor-agnostic monitoring source collectors.

Each vendor integration is a `BaseCollector` subclass that registers itself
against a `source_type` string via `@CollectorRegistry.register("prtg")`.
Callers only ever ask `CollectorRegistry.get(source.source_type)` - they
never import a vendor class directly, so adding vendor #11 means adding one
new file, not editing the orchestrator, the API, or the registry itself.
"""

"""CollectorHealthService: failure categorization + test-source exclusion."""
from __future__ import annotations

import pytest

from app.core.db import AsyncSessionLocal
from app.services.collector_health_service import (
    TEST_SOURCE_TYPES,
    CollectorHealthService,
    categorize_failure,
)


def test_categorize_failure_buckets_reasons():
    assert categorize_failure(None) is None
    assert categorize_failure("") is None
    assert categorize_failure("authentication failed: invalid credentials") == "credentials"
    assert categorize_failure("HTTP 401 Unauthorized") == "credentials"
    assert categorize_failure("host unreachable: timed out") == "unreachable"
    assert categorize_failure("connection refused") == "unreachable"
    assert categorize_failure("no collector registered for this source") == "no_collector_registered"
    assert categorize_failure("something unexpected happened") == "other"


def test_test_source_types_are_an_explicit_allowlist():
    # The exclusion is an explicit, documented set - not a substring heuristic.
    assert "test_fake_concurrency" in TEST_SOURCE_TYPES


@pytest.mark.asyncio
async def test_exclude_test_sources_filters_synthetic_sources(tenant, monitoring_source_factory):
    test_source = await monitoring_source_factory(source_type="test_fake_concurrency")
    real_source = await monitoring_source_factory(source_type="prtg")

    async with AsyncSessionLocal() as db:
        service = CollectorHealthService(db)
        included = await service.get_health_for_tenant(tenant.id, exclude_test_sources=False)
        excluded = await service.get_health_for_tenant(tenant.id, exclude_test_sources=True)

    included_ids = {str(h.source_id) for h in included}
    excluded_ids = {str(h.source_id) for h in excluded}

    # With the filter off, the synthetic source is present (nothing is deleted).
    assert str(test_source.id) in included_ids
    assert str(real_source.id) in included_ids
    # With the filter on, the synthetic source is dropped; the real one stays.
    assert str(test_source.id) not in excluded_ids
    assert str(real_source.id) in excluded_ids

"""
Tests for the canonical Infrastructure Overview aggregation.

Two layers:
  * Pure unit tests for the status vocabulary / bucketing and the per-site
    rollup, which need no database.
  * DB-backed tests asserting the *internal consistency* invariant that makes
    the overview usable as a single source of truth: every set of buckets must
    sum to its total, so no page can ever be shown a breakdown that does not
    add up.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

from app.models.device import Device
from app.models.site import Site
from app.repositories.device_repository import DeviceRepository
from app.repositories.incident_repository import IncidentRepository
from app.repositories.resource_relationship_repository import (
    ResourceRelationshipRepository,
)
from app.repositories.sensor_repository import SensorRepository
from app.repositories.site_repository import SiteRepository
from app.services.infrastructure_service import InfrastructureService
from app.services.infrastructure_overview_service import (
    HEALTH_CRITICAL,
    HEALTH_HEALTHY,
    HEALTH_UNKNOWN,
    HEALTH_WARNING,
    NOT_DISCOVERED,
    InfrastructureHealth,
    InfrastructureOverviewService,
    device_health_bucket,
    sensor_health_bucket,
)


class TestDeviceHealthBucket:
    """The live DB stores BOTH 'up' and 'online' for a healthy device
    (131 'up' + 93 'online' observed). Treating only 'up' as healthy - as
    schemas/site.py's SiteSummary did - silently under-counts every device a
    collector marked 'online'."""

    def test_online_is_healthy_not_unknown(self):
        assert device_health_bucket("online") == HEALTH_HEALTHY

    def test_up_is_healthy(self):
        assert device_health_bucket("up") == HEALTH_HEALTHY

    def test_case_and_whitespace_insensitive(self):
        assert device_health_bucket("  UP ") == HEALTH_HEALTHY
        assert device_health_bucket("Online") == HEALTH_HEALTHY

    def test_warning_variants(self):
        for value in ("warning", "degraded", "alerting", "maintenance"):
            assert device_health_bucket(value) == HEALTH_WARNING, value

    def test_critical_variants(self):
        for value in ("down", "offline", "critical", "failed", "error"):
            assert device_health_bucket(value) == HEALTH_CRITICAL, value

    def test_unknown_values_are_never_counted_as_healthy(self):
        """Unrecognised status must not be optimistically bucketed."""
        for value in (None, "", "   ", "some_new_vendor_state", "??"):
            assert device_health_bucket(value) == HEALTH_UNKNOWN, value

    def test_healthy_and_critical_sets_are_disjoint(self):
        from app.services.infrastructure_overview_service import (
            DEVICE_STATUS_CRITICAL,
            DEVICE_STATUS_HEALTHY,
            DEVICE_STATUS_WARNING,
        )

        assert not (DEVICE_STATUS_HEALTHY & DEVICE_STATUS_CRITICAL)
        assert not (DEVICE_STATUS_HEALTHY & DEVICE_STATUS_WARNING)
        assert not (DEVICE_STATUS_WARNING & DEVICE_STATUS_CRITICAL)


class TestSensorHealthBucket:
    def test_up_warning_down(self):
        assert sensor_health_bucket("up") == HEALTH_HEALTHY
        assert sensor_health_bucket("warning") == HEALTH_WARNING
        assert sensor_health_bucket("down") == HEALTH_CRITICAL

    def test_unknown_stays_unknown(self):
        assert sensor_health_bucket("unknown") == HEALTH_UNKNOWN
        assert sensor_health_bucket(None) == HEALTH_UNKNOWN


class TestHealthDataclass:
    def test_defaults_are_zero_not_none(self):
        health = InfrastructureHealth().as_dict()
        assert all(value == 0 for value in health.values())
        assert "total_devices" in health
        assert "open_incidents" in health
        assert "total_interfaces" in health


# ---------------------------------------------------------------------------
# Per-site rollup (pure - _site_block performs no I/O)
# ---------------------------------------------------------------------------

def _site(name: str, *, lat=None, lng=None):
    return SimpleNamespace(
        id=uuid.uuid4(), name=name, code=name.upper(), address=None,
        city=None, country=None, region=None, site_type=None, criticality=None,
        latitude=lat, longitude=lng,
    )


def _device(status, site_id=None):
    return SimpleNamespace(status=status, site_id=site_id, device_type="switch")


def _service() -> InfrastructureOverviewService:
    # _site_block/_device_block touch no DB, so a db-less instance is fine.
    return InfrastructureOverviewService(db=None)  # type: ignore[arg-type]


class TestSiteBlock:

    def test_site_with_only_healthy_devices_is_healthy(self):
        site = _site("Lab")
        health = InfrastructureHealth()
        rows, _ = _service()._site_block(
            [site], [_device("up", site.id), _device("online", site.id)], health
        )
        assert rows[0]["health"] == HEALTH_HEALTHY
        assert rows[0]["devices_healthy"] == 2
        assert health.healthy_sites == 1

    def test_site_with_critical_device_is_critical(self):
        site = _site("Lab")
        health = InfrastructureHealth()
        rows, _ = _service()._site_block(
            [site], [_device("up", site.id), _device("down", site.id)], health
        )
        assert rows[0]["health"] == HEALTH_CRITICAL
        assert health.critical_sites == 1

    def test_site_with_warning_device_is_warning(self):
        site = _site("Lab")
        health = InfrastructureHealth()
        rows, _ = _service()._site_block(
            [site], [_device("up", site.id), _device("warning", site.id)], health
        )
        assert rows[0]["health"] == HEALTH_WARNING

    def test_site_with_no_devices_is_unknown_not_healthy(self):
        """No devices is no evidence - calling it healthy would invent a signal."""
        site = _site("Empty")
        health = InfrastructureHealth()
        rows, _ = _service()._site_block([site], [], health)
        assert rows[0]["health"] == HEALTH_UNKNOWN
        assert rows[0]["devices_total"] == 0
        assert health.unknown_sites == 1
        assert health.healthy_sites == 0

    def test_devices_without_site_do_not_pollute_a_site(self):
        site = _site("Lab")
        health = InfrastructureHealth()
        rows, _ = _service()._site_block([site], [_device("up", None)], health)
        assert rows[0]["devices_total"] == 0

    def test_site_buckets_always_sum_to_devices_total(self):
        site = _site("Lab")
        health = InfrastructureHealth()
        devices = [_device(s, site.id)
                   for s in ("up", "online", "warning", "down", "unknown", None)]
        rows, _ = _service()._site_block([site], devices, health)
        row = rows[0]
        assert (row["devices_healthy"] + row["devices_warning"]
                + row["devices_critical"] + row["devices_unknown"]
                == row["devices_total"] == len(devices))


class TestGeographyBlock:
    """Phase 4 / Phase 16: coordinates are never derived from anything else."""

    def test_site_with_coordinates_is_mapped(self):
        site = _site("HQ", lat=30.7333, lng=76.7794)
        health = InfrastructureHealth()
        _, geo = _service()._site_block([site], [], health)
        assert geo["sites_with_coordinates"] == 1
        assert geo["mapped"][0]["latitude"] == 30.7333
        assert geo["mapped"][0]["longitude"] == 76.7794
        assert geo["location_unavailable"] is False

    def test_site_without_coordinates_is_never_mapped(self):
        site = _site("Private IP site")
        health = InfrastructureHealth()
        _, geo = _service()._site_block([site], [], health)
        assert geo["sites_with_coordinates"] == 0
        assert geo["mapped"] == []
        assert geo["sites_without_coordinates"] == 1
        assert geo["location_unavailable"] is True

    def test_partial_coordinates_are_not_treated_as_mappable(self):
        """lat without lng must not place a marker on the equator."""
        site = _site("Half", lat=30.7333, lng=None)
        health = InfrastructureHealth()
        _, geo = _service()._site_block([site], [], health)
        assert geo["mapped"] == []
        assert geo["sites_without_coordinates"] == 1

    def test_private_ip_is_never_geocoded(self):
        """A site whose only location hint is a private IP must stay unmapped."""
        site = _site("192.168.1.0/24")
        health = InfrastructureHealth()
        _, geo = _service()._site_block([site], [], health)
        assert geo["mapped"] == []
        assert geo["location_unavailable"] is True


class TestDeviceBlock:
    """Raw vocabulary is published alongside the buckets so a total is auditable."""

    def test_buckets_and_raw_vocabulary_agree(self):
        devices = [_device("up"), _device("online"), _device("down"),
                   _device("warning"), _device("unknown"), _device(None)]
        health = InfrastructureHealth()
        block = _service()._device_block(devices, health)  # type: ignore[arg-type]

        assert block["total"] == 6
        assert block["by_status"] == {
            "up": 1, "online": 1, "down": 1, "warning": 1, "unknown": 2,
        }
        assert health.healthy_devices == 2   # up + online
        assert health.critical_devices == 1
        assert health.warning_devices == 1
        assert health.unknown_devices == 2
        assert (health.healthy_devices + health.warning_devices
                + health.critical_devices + health.unknown_devices
                == block["total"])

    def test_unclassified_raw_types_are_reported_canonically(self):
        """'other'/'unknown' land in the canonical `unclassified` bucket while
        the raw spelling is preserved for provenance."""
        devices = [_device("up"), _device("up")]
        devices[0].device_type = "other"
        devices[1].device_type = "unknown"
        block = _service()._device_block(  # type: ignore[arg-type]
            devices, InfrastructureHealth()
        )
        assert block["by_type"] == {"unclassified": 2}
        assert block["by_type_raw"] == {"other": 1, "unknown": 1}

    def test_unassigned_devices_are_counted(self):
        devices = [_device("up", uuid.uuid4()), _device("up", None)]
        block = _service()._device_block(  # type: ignore[arg-type]
            devices, InfrastructureHealth()
        )
        assert block["unassigned_to_site"] == 1

    def test_empty_tenant_reports_zero_not_error(self):
        health = InfrastructureHealth()
        block = _service()._device_block([], health)  # type: ignore[arg-type]
        assert block["total"] == 0
        assert block["by_status"] == {}
        assert health.total_devices == 0


# ---------------------------------------------------------------------------
# Cross-page consistency against the real database (read-only)
# ---------------------------------------------------------------------------

class TestOverviewConsistency:
    """Phase 18 invariants. These are the checks that make the overview usable
    as a single source of truth: the overview must agree with the same
    repository a page-level endpoint uses, for every count they share.
    """

    async def _overview(self, db, tenant):
        return await InfrastructureOverviewService(db).build(tenant.id)

    async def test_device_buckets_sum_to_total(self, db, tenant):
        h = (await self._overview(db, tenant))["health"]
        assert (h["healthy_devices"] + h["warning_devices"]
                + h["critical_devices"] + h["unknown_devices"]
                == h["total_devices"])

    async def test_site_buckets_sum_to_total(self, db, tenant):
        h = (await self._overview(db, tenant))["health"]
        assert (h["healthy_sites"] + h["warning_sites"] + h["critical_sites"]
                + h["unknown_sites"] == h["total_sites"])

    async def test_sensor_buckets_sum_to_total(self, db, tenant):
        h = (await self._overview(db, tenant))["health"]
        assert (h["healthy_sensors"] + h["warning_sensors"] + h["down_sensors"]
                + h["unknown_sensors"] == h["total_sensors"])

    async def test_alert_severities_sum_to_total(self, db, tenant):
        h = (await self._overview(db, tenant))["health"]
        assert (h["critical_alerts"] + h["warning_alerts"] + h["info_alerts"]
                == h["total_alerts"])

    async def test_device_total_matches_device_repository(self, db, tenant):
        """Dashboard device count == infrastructure inventory count."""
        overview = await self._overview(db, tenant)
        assert overview["health"]["total_devices"] == await DeviceRepository(
            db
        ).count(tenant.id)
        assert overview["devices"]["total"] == overview["health"]["total_devices"]

    async def test_sensor_total_matches_sensor_repository(self, db, tenant):
        """Monitoring sensor count == sensor inventory count."""
        overview = await self._overview(db, tenant)
        assert overview["health"]["total_sensors"] == await SensorRepository(
            db
        ).count_for_tenant(tenant.id)

    async def test_relationship_total_matches_repository(self, db, tenant):
        overview = await self._overview(db, tenant)
        edges = await ResourceRelationshipRepository(db).count(tenant.id)
        assert overview["health"]["total_relationships"] == edges
        assert overview["topology"]["edges_total"] == edges
        assert overview["topology"]["has_edges"] is (edges > 0)

    async def test_incident_totals_match_repository(self, db, tenant):
        overview = await self._overview(db, tenant)
        repo = IncidentRepository(db)
        assert overview["health"]["total_incidents"] == await repo.count(tenant.id)
        assert overview["health"]["open_incidents"] == await repo.count(
            tenant.id, status="open"
        )

    async def test_geography_mapped_sites_have_real_coordinates(self, db, tenant):
        """No mapped site may lack latitude/longitude (no fabricated markers)."""
        geo = (await self._overview(db, tenant))["geography"]
        assert (geo["sites_with_coordinates"] + geo["sites_without_coordinates"]
                == len(geo["mapped"]) + geo["sites_without_coordinates"])
        for site in geo["mapped"]:
            assert site["latitude"] is not None
            assert site["longitude"] is not None

    async def test_topology_reports_not_discovered_when_empty(self, db, tenant):
        """With no relationship rows the graph must say so, not look blank."""
        topology = (await self._overview(db, tenant))["topology"]
        if topology["edges_total"] == 0:
            assert topology["status"] == NOT_DISCOVERED
            assert "note" in topology

    async def test_monitoring_source_never_healthy_when_latest_run_failed(
        self, db, tenant
    ):
        """Phase 9: collector evidence outranks the stored health_status."""
        block = (await self._overview(db, tenant))["monitoring_sources"]
        for source in block["items"]:
            latest = source["latest_run"]
            if latest is not None and latest["status"] in ("error", "partial"):
                assert source["effective_health"] == "failing", source["name"]

    async def test_collector_success_rate_matches_its_window(self, db, tenant):
        """Every published rate must be reproducible from its own counts."""
        runs = (await self._overview(db, tenant))["collector_runs"]
        for label, counts in runs["windows"].items():
            attempted = counts["success"] + counts["partial"] + counts["error"]
            assert counts["attempted"] == attempted, label
            if attempted:
                expected = round(counts["success"] / attempted * 100, 1)
                assert counts["success_rate"] == expected, label
            else:
                assert counts["success_rate"] is None, label

    async def test_last_updated_is_present(self, db, tenant):
        overview = await self._overview(db, tenant)
        assert overview["last_updated"]
        assert overview["tenant_id"] == str(tenant.id)


# ---------------------------------------------------------------------------
# Step 2 data consistency: the per-site endpoint must agree with the canonical
# overview about what "online" means.
# ---------------------------------------------------------------------------

class TestSiteSummaryCanonicalBuckets:
    """`/infrastructure/sites` used to read `status_counts.get("up", 0)`, so a
    device stored as "online" - which the live database does 93 times - was
    invisible at site level while the canonical overview counted it. Both now
    share `device_health_bucket`, and these tests pin them together.

    Fixture order is `(tenant, db)` deliberately, NOT `(db, tenant)`. pytest
    tears fixtures down in reverse setup order, and `db` holds uncommitted
    INSERTs that reference the tenant row (a FK KEY SHARE lock). With
    `(db, tenant)` the tenant teardown's `DELETE FROM tenants` waits on that
    lock while `db` waits for the DELETE to finish - a deadlock that hung the
    suite. Setting `db` up after `tenant` makes the rollback happen first.
    """

    async def _seed_site(self, db, tenant, statuses, code_suffix):
        site = Site(
            tenant_id=tenant.id,
            name=f"Canonical Test Site {code_suffix}",
            code=f"CTS-{code_suffix}",
        )
        db.add(site)
        await db.flush()
        for index, status in enumerate(statuses):
            db.add(Device(
                tenant_id=tenant.id,
                name=f"cts-{code_suffix}-{index}",
                device_type="switch",
                status=status,
                site_id=site.id,
            ))
        await db.flush()
        return site

    async def _summaries(self, db, tenant):
        service = InfrastructureService(SiteRepository(db), DeviceRepository(db))
        return await service.list_site_summaries(tenant.id)

    async def test_online_devices_count_as_online(self, tenant, db):
        """The regression: 'online' must not be treated as not-healthy."""
        site = await self._seed_site(
            db, tenant, ["up", "online", "online"], uuid.uuid4().hex[:8]
        )
        row = next(s for s in await self._summaries(db, tenant) if s.id == site.id)
        assert row.devices_total == 3
        assert row.devices_online == 3, "online devices must be counted online"
        assert row.devices_offline == 0

    async def test_critical_bucket_covers_more_than_down(self, tenant, db):
        site = await self._seed_site(
            db, tenant, ["down", "offline", "failed"], uuid.uuid4().hex[:8]
        )
        row = next(s for s in await self._summaries(db, tenant) if s.id == site.id)
        assert row.devices_offline == 3

    async def test_buckets_sum_to_total(self, tenant, db):
        """The four device fields plus unknown must reconcile with the total.

        `Device.status` is NOT NULL, so the un-storable NULL case is covered by
        the pure unit tests rather than by a row here.
        """
        statuses = ["up", "online", "warning", "down", "critical", "unknown"]
        site = await self._seed_site(
            db, tenant, statuses, uuid.uuid4().hex[:8]
        )
        row = next(s for s in await self._summaries(db, tenant) if s.id == site.id)
        assert (
            row.devices_online + row.devices_warning + row.devices_offline
            + row.devices_critical + row.devices_unknown
            == row.devices_total == len(statuses)
        )

    async def test_site_summary_matches_canonical_overview_for_site_devices(
        self, tenant, db
    ):
        """Cross-endpoint consistency: both must bucket identically."""
        statuses = ["up", "online", "warning", "down"]
        site = await self._seed_site(
            db, tenant, statuses, uuid.uuid4().hex[:8]
        )
        row = next(s for s in await self._summaries(db, tenant) if s.id == site.id)

        overview = await InfrastructureOverviewService(db).build(tenant.id)
        canonical = next(
            s for s in overview["sites"] if s["site_id"] == str(site.id)
        )
        assert row.devices_online == canonical["devices_healthy"]
        assert row.devices_warning == canonical["devices_warning"]
        assert row.devices_offline == canonical["devices_critical"]
        assert row.devices_unknown == canonical["devices_unknown"]

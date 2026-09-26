"""
Phase 4 tests: cross-source device identity resolution (§16/§17) and the
ENTITY-MIB chassis-serial selection that feeds it.

The risk this guards against is a WRONG MERGE - attributing one device's
interfaces, sensors and alerts to another. Every test here therefore checks both
directions: that the same device DOES collapse to one row, and that different
devices (or placeholder serials, or another tenant's device) do NOT.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.repositories.device_repository import DeviceRepository
from app.services.discovery.identity import is_authoritative, normalise_serial
from app.collectors.switching import select_chassis_identity


class TestSerialNormalisation:

    def test_case_and_whitespace_collapse(self):
        assert normalise_serial("  fdo1234abcd ") == "FDO1234ABCD"
        assert normalise_serial("fdo1234abcd") == "FDO1234ABCD"
        # Internal runs of whitespace collapse to a single space, so the same
        # serial written differently by two vendors still merges.
        assert normalise_serial("fdo  1234\tabcd") == "FDO 1234 ABCD"

    def test_placeholder_serials_carry_no_identity(self):
        for junk in (
            "", "   ", None, "None", "unknown", "N/A",
            "To be filled by O.E.M.", "To be filled by OEM",
            "Default string", "System Serial Number", "00000000", "0",
        ):
            assert normalise_serial(junk) is None, junk
            assert is_authoritative(junk) is False, junk

    def test_real_serial_is_authoritative(self):
        assert is_authoritative("FDO1234ABCD") is True


class TestChassisIdentitySelection:
    """ENTITY-MIB serials exist for chassis, line cards, PSUs and fans."""

    def test_chassis_serial_is_preferred(self):
        serial, model, name = select_chassis_identity(
            classes={"1": "3", "2": "9"},          # 3=chassis, 9=module
            serials={"1": "CHASSIS-SN", "2": "LINECARD-SN"},
            models={"1": "C9300-48P", "2": "C9300-NM-8X"},
            names={"1": "Chassis", "2": "Slot 1"},
        )
        assert serial == "CHASSIS-SN"
        assert model == "C9300-48P"
        assert name == "Chassis"

    def test_chassis_without_serial_yields_no_serial(self):
        """A chassis row that reports no serial must not fall back to a line
        card's serial: that would give the device an unstable identity."""
        serial, model, _ = select_chassis_identity(
            classes={"1": "3", "2": "9"},
            serials={"2": "LINECARD-SN"},
            models={"1": "C9300-48P"},
        )
        assert serial is None
        assert model == "C9300-48P"

    def test_single_serial_without_class_information_is_used(self):
        serial, _, _ = select_chassis_identity(
            classes={}, serials={"1": "ONLY-SN"},
        )
        assert serial == "ONLY-SN"

    def test_multiple_serials_without_class_information_are_ambiguous(self):
        """Two candidate serials and no class column -> NO identity, rather than
        picking one at random."""
        serial, model, name = select_chassis_identity(
            classes={}, serials={"1": "SN-A", "2": "SN-B"},
            models={"1": "M1"}, names={"1": "N1"},
        )
        assert serial is None and model is None and name is None

    def test_empty_input_is_safe(self):
        assert select_chassis_identity({}, {}) == (None, None, None)
        assert select_chassis_identity({}, {"1": ""}) == (None, None, None)


# ---------------------------------------------------------------------------
# Cross-source identity at the repository level (§16/§17/§19/§20)
#
# Fixture order (tenant, monitoring_source_factory, db) keeps `db` teardown
# first, so its uncommitted FK locks release before the tenant cascade.
# ---------------------------------------------------------------------------

async def _upsert(db, tenant, source, *, external_id, serial=None, name="dev"):
    return await DeviceRepository(db).upsert(
        tenant_id=tenant.id,
        monitoring_source_id=source.id,
        external_id=external_id,
        name=name,
        device_type="switch",
        serial_number=serial,
    )


class TestCrossSourceIdentity:

    async def test_same_serial_from_two_sources_is_one_device(
        self, tenant, monitoring_source_factory, db
    ):
        """FortiGate via SNMP and FortiGate via API must not become two devices."""
        snmp_source = await monitoring_source_factory(
            source_type="snmp", hostname="10.0.0.1"
        )
        api_source = await monitoring_source_factory(
            source_type="fortigate", hostname="10.0.0.1"
        )

        first, created_first = await _upsert(
            db, tenant, snmp_source, external_id="fw-1", serial="FGT-SN-001"
        )
        second, created_second = await _upsert(
            db, tenant, api_source, external_id="fgt-root", serial="FGT-SN-001"
        )

        assert created_first is True
        assert created_second is False
        assert second.id == first.id
        assert await DeviceRepository(db).count(tenant.id) == 1

    async def test_merge_is_recorded_in_metadata(
        self, tenant, monitoring_source_factory, db
    ):
        snmp_source = await monitoring_source_factory(
            source_type="snmp", hostname="10.0.0.1"
        )
        api_source = await monitoring_source_factory(
            source_type="fortigate", hostname="10.0.0.1"
        )
        await _upsert(db, tenant, snmp_source, external_id="fw-1", serial="SN-X")
        device, _ = await _upsert(
            db, tenant, api_source, external_id="api-1", serial="SN-X"
        )
        assert str(api_source.id) in device.custom_metadata["observed_by_source_ids"]

    async def test_owning_source_keys_are_not_reassigned(
        self, tenant, monitoring_source_factory, db
    ):
        """Critical for idempotency: if the merge stole (source, external_id),
        the ORIGINAL source would stop finding its device and create a duplicate
        on its very next poll."""
        source_a = await monitoring_source_factory(
            source_type="snmp", hostname="10.0.0.1"
        )
        source_b = await monitoring_source_factory(
            source_type="fortigate", hostname="10.0.0.1"
        )
        device, _ = await _upsert(db, tenant, source_a, external_id="a-1", serial="SN-Y")
        merged, _ = await _upsert(db, tenant, source_b, external_id="b-1", serial="SN-Y")
        assert merged.id == device.id
        assert merged.monitoring_source_id == source_a.id
        assert merged.external_id == "a-1"

    async def test_repeated_polling_by_both_sources_stays_one_device(
        self, tenant, monitoring_source_factory, db
    ):
        """Idempotency (§19): three rounds from two collectors, still one row."""
        source_a = await monitoring_source_factory(
            source_type="snmp", hostname="10.0.0.1"
        )
        source_b = await monitoring_source_factory(
            source_type="wmi", hostname="10.0.0.1"
        )
        for _ in range(3):
            await _upsert(db, tenant, source_a, external_id="a-1", serial="SN-Z")
            await _upsert(db, tenant, source_b, external_id="b-1", serial="SN-Z")
        assert await DeviceRepository(db).count(tenant.id) == 1

    async def test_different_serials_stay_separate_devices(
        self, tenant, monitoring_source_factory, db
    ):
        source_a = await monitoring_source_factory(
            source_type="snmp", hostname="10.0.0.1"
        )
        source_b = await monitoring_source_factory(
            source_type="snmp", hostname="10.0.0.2"
        )
        await _upsert(db, tenant, source_a, external_id="a-1", serial="SN-AAA")
        await _upsert(db, tenant, source_b, external_id="b-1", serial="SN-BBB")
        assert await DeviceRepository(db).count(tenant.id) == 2

    async def test_no_serial_keeps_existing_source_scoped_behaviour(
        self, tenant, monitoring_source_factory, db
    ):
        """Without authoritative identity two sources remain two devices -
        unchanged from before, and never merged on hostname or IP."""
        source_a = await monitoring_source_factory(
            source_type="snmp", hostname="10.0.0.1"
        )
        source_b = await monitoring_source_factory(
            source_type="wmi", hostname="10.0.0.1"
        )
        await _upsert(db, tenant, source_a, external_id="same-name")
        await _upsert(db, tenant, source_b, external_id="same-name")
        assert await DeviceRepository(db).count(tenant.id) == 2

    async def test_placeholder_serial_never_merges(
        self, tenant, monitoring_source_factory, db
    ):
        """Vendors returning 'To be filled by O.E.M.' must not collapse the
        estate into a single device."""
        source_a = await monitoring_source_factory(
            source_type="snmp", hostname="10.0.0.1"
        )
        source_b = await monitoring_source_factory(
            source_type="snmp", hostname="10.0.0.2"
        )
        await _upsert(db, tenant, source_a, external_id="a-1",
                      serial="To be filled by O.E.M.")
        await _upsert(db, tenant, source_b, external_id="b-1",
                      serial="To be filled by O.E.M.")
        assert await DeviceRepository(db).count(tenant.id) == 2

    async def test_serial_case_differences_still_merge(
        self, tenant, monitoring_source_factory, db
    ):
        source_a = await monitoring_source_factory(
            source_type="snmp", hostname="10.0.0.1"
        )
        source_b = await monitoring_source_factory(
            source_type="wmi", hostname="10.0.0.1"
        )
        await _upsert(db, tenant, source_a, external_id="a-1", serial="fdo1234abcd")
        merged, created = await _upsert(
            db, tenant, source_b, external_id="b-1", serial=" FDO1234ABCD "
        )
        assert created is False
        assert merged.serial_number == "FDO1234ABCD"
        assert await DeviceRepository(db).count(tenant.id) == 1

    async def test_same_serial_in_another_tenant_is_not_merged(
        self, tenant, monitoring_source_factory, db
    ):
        """Tenant isolation (§20): a serial is only unique WITHIN a tenant."""
        from app.models.monitoring_source import MonitoringSource
        from app.models.tenant import Tenant

        source_a = await monitoring_source_factory(
            source_type="snmp", hostname="10.0.0.1"
        )
        await _upsert(db, tenant, source_a, external_id="a-1", serial="SHARED-SN")

        other = Tenant(name="Other", slug=f"other-{uuid.uuid4().hex[:8]}")
        db.add(other)
        await db.flush()
        other_source = MonitoringSource(
            tenant_id=other.id, name="other-snmp", source_type="snmp",
            hostname="10.0.0.9", is_active=True, poll_interval_seconds=60,
            status="pending",
        )
        db.add(other_source)
        await db.flush()

        _merged, created = await _upsert(
            db, other, other_source, external_id="b-1", serial="SHARED-SN"
        )
        assert created is True
        assert await DeviceRepository(db).count(other.id) == 1
        assert await DeviceRepository(db).count(tenant.id) == 1

    async def test_known_serial_is_not_blanked_by_a_later_poll(
        self, tenant, monitoring_source_factory, db
    ):
        """A later poll that cannot read the serial must not erase identity.

        The scenario is the SAME source re-polling: with no serial there is no
        evidence linking two DIFFERENT sources, so those legitimately stay two
        devices (merging them on hostname/IP is forbidden by §18).
        """
        source = await monitoring_source_factory(
            source_type="snmp", hostname="10.0.0.1"
        )
        device, _ = await _upsert(
            db, tenant, source, external_id="sw-1", serial="KEEP-ME"
        )
        assert device.serial_number == "KEEP-ME"

        # Same source, same external_id, but this poll could not read a serial.
        again, created = await _upsert(
            db, tenant, source, external_id="sw-1", serial=None
        )
        assert created is False
        assert again.id == device.id
        assert again.serial_number == "KEEP-ME"
        assert await DeviceRepository(db).count(tenant.id) == 1

    async def test_lookup_by_serial_is_tenant_scoped(
        self, tenant, monitoring_source_factory, db
    ):
        source = await monitoring_source_factory(source_type="snmp", hostname="10.0.0.1")
        await _upsert(db, tenant, source, external_id="a-1", serial="FIND-ME")
        repo = DeviceRepository(db)
        assert await repo.get_by_serial_number(tenant.id, "find-me") is not None
        assert await repo.get_by_serial_number(uuid.uuid4(), "FIND-ME") is None
        assert await repo.get_by_serial_number(tenant.id, "nope") is None


# ---------------------------------------------------------------------------
# End-to-end: the SAME host collected by WMI and by SNMP becomes one Device.
#
# This is the whole purpose of Phase 4.1 - each collector reads its serial from a
# different protocol (Win32_BIOS vs ENTITY-MIB), and the existing repository
# cross-source identity must collapse them.
# ---------------------------------------------------------------------------

def _saver(db):
    from app.repositories.event_repository import EventRepository
    from app.repositories.interface_repository import InterfaceRepository
    from app.repositories.metric_repository import MetricRepository
    from app.repositories.sensor_repository import SensorRepository
    from app.services.monitoring.bundle_saver import BundleSaver

    return BundleSaver(
        device_repo=DeviceRepository(db),
        interface_repo=InterfaceRepository(db),
        sensor_repo=SensorRepository(db),
        metric_repo=MetricRepository(db),
        event_repo=EventRepository(db),
    )


def _wmi_bundle(serial, hostname="SRV-APP01"):
    """Raw WMI payload -> the real WMI collector's normalize()."""
    from app.collectors.wmi import WMICollector

    source = SimpleNamespace(
        id=uuid.uuid4(), tenant_id=uuid.uuid4(), name="wmi-src", source_type="wmi",
        hostname=hostname, ip_address=hostname, port=None, protocol="http",
        snmp_version=None, username="u", api_key=None, password="p",
        config={}, verify_ssl=False,
    )
    raw = {
        "computer_system": [{"Name": hostname, "Domain": "corp.local"}],
        "os": [{"Caption": "Microsoft Windows Server 2022 Standard",
                "Version": "10.0.20348", "BuildNumber": "20348",
                "TotalVisibleMemorySize": 100, "FreePhysicalMemory": 50}],
        "bios": [{"SerialNumber": serial}],
    }
    return WMICollector(source).normalize(raw)


def _snmp_bundle(serial, hostname="10.0.0.1"):
    """Raw SNMP payload (ENTITY-MIB) -> the real SNMP collector's normalize()."""
    from app.collectors.snmp import SNMPCollector

    source = SimpleNamespace(
        id=uuid.uuid4(), tenant_id=uuid.uuid4(), name="snmp-src", source_type="snmp",
        hostname=hostname, ip_address=hostname, port=None, protocol="snmp",
        snmp_version="v2c", username=None, api_key="public", password=None,
        config={}, verify_ssl=False,
    )
    raw = {
        "sys_name": hostname, "sys_descr": "Some OS", "interfaces": {"descr": {}},
        "lldp": {}, "cdp": {}, "lldp_status": "unavailable", "cdp_status": "unavailable",
        "switching": {"status": {}},
        "identity": {"serial_number": serial, "status": "collected"},
    }
    return SNMPCollector(source).normalize(raw)


class TestWmiSnmpConvergence:

    async def test_wmi_and_snmp_serials_resolve_to_one_device(
        self, tenant, monitoring_source_factory, db
    ):
        """§17: a Windows server polled by WMI and by SNMP must not be two devices."""
        wmi_source = await monitoring_source_factory(source_type="wmi", hostname="10.0.0.50")
        snmp_source = await monitoring_source_factory(source_type="snmp", hostname="10.0.0.50")
        saver = _saver(db)

        wmi_bundle = _wmi_bundle("HOST-SERIAL-9000")
        snmp_bundle = _snmp_bundle("HOST-SERIAL-9000")

        # Assert the two collectors really did extract the serial, so this test
        # cannot pass merely because both produced None.
        assert wmi_bundle.devices[0].serial_number == "HOST-SERIAL-9000"
        assert snmp_bundle.devices[0].serial_number == "HOST-SERIAL-9000"

        await saver.save(wmi_source, wmi_bundle)
        before = await DeviceRepository(db).count(tenant.id)
        assert before == 1

        await saver.save(snmp_source, snmp_bundle)
        assert await DeviceRepository(db).count(tenant.id) == 1, (
            "the same host seen by WMI and SNMP must be ONE device"
        )

        device = await DeviceRepository(db).get_by_serial_number(
            tenant.id, "HOST-SERIAL-9000"
        )
        assert device is not None
        # The WMI source owns it, because it observed it first; the SNMP poll
        # enriched it rather than stealing the ownership keys.
        assert device.monitoring_source_id == wmi_source.id

    async def test_placeholder_wmi_serial_does_not_merge_with_snmp(
        self, tenant, monitoring_source_factory, db
    ):
        """A board reporting 'To be filled by O.E.M.' must not silently join an
        unrelated device."""
        wmi_source = await monitoring_source_factory(source_type="wmi", hostname="10.0.0.50")
        snmp_source = await monitoring_source_factory(source_type="snmp", hostname="10.0.0.60")
        saver = _saver(db)

        wmi_bundle = _wmi_bundle("To be filled by O.E.M.")
        snmp_bundle = _snmp_bundle("REAL-SERIAL-XYZ")

        assert wmi_bundle.devices[0].serial_number is None
        assert snmp_bundle.devices[0].serial_number == "REAL-SERIAL-XYZ"

        await saver.save(wmi_source, wmi_bundle)
        await saver.save(snmp_source, snmp_bundle)
        assert await DeviceRepository(db).count(tenant.id) == 2

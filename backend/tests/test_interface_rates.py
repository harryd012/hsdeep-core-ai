"""Two-sample interface rate tests (Phase 9.8, step 8).

The rate engine is pure, so every honesty rule is asserted directly: one sample
is never a rate, a counter reset is never a negative rate, and utilisation needs
a speed that is actually plausible. The last test proves the wiring into the
existing save path, against the real database.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services.monitoring import interface_rates as R

pytestmark = pytest.mark.asyncio

NOW = datetime(2026, 9, 26, 12, 0, 0, tzinfo=timezone.utc)


def rate(**kwargs):
    base = {
        "previous_in": 1000.0,
        "previous_out": 2000.0,
        "previous_at": NOW - timedelta(seconds=60),
        "current_in": 1000.0,
        "current_out": 2000.0,
        "observed_at": NOW,
        "speed_mbps": 100.0,
    }
    base.update(kwargs)
    return R.compute_interface_rate(**base)


class TestRateMath:
    async def test_two_samples_produce_bits_per_second(self):
        result = rate(current_in=1000.0 + 75_000, current_out=2000.0 + 37_500)
        assert result.rx_bps == pytest.approx(75000 * 8 / 60)
        assert result.tx_bps == pytest.approx(37500 * 8 / 60)
        assert result.measured is True
        assert result.has_counters is True
        # 10 000 bps on a 100 Mbps link.
        assert result.utilization_pct == pytest.approx(0.01, abs=1e-6)

    async def test_a_single_sample_is_never_a_rate(self):
        result = rate(previous_at=None, previous_in=None, previous_out=None)
        assert result.rx_bps is None and result.tx_bps is None
        assert result.utilization_pct is None
        assert result.reason == R.REASON_NO_PREVIOUS_SAMPLE
        # The counters were real, so freshness is still recorded.
        assert result.has_counters is True

    async def test_no_counters_at_all_is_reported(self):
        result = rate(current_in=None, current_out=None, previous_in=None, previous_out=None)
        assert result.measured is False
        assert result.reason == R.REASON_NO_COUNTERS
        assert result.has_counters is False

    async def test_a_counter_reset_never_yields_a_negative_rate(self):
        reset = rate(previous_in=10_000.0, current_in=5.0,
                     previous_out=10_000.0, current_out=5.0)
        assert reset.rx_bps is None and reset.tx_bps is None
        assert reset.reason == R.REASON_COUNTER_RESET

    async def test_only_the_reset_direction_loses_its_rate(self):
        result = rate(previous_in=10_000.0, current_in=5.0,
                      previous_out=2000.0, current_out=2000.0 + 7500)
        assert result.rx_bps is None
        assert result.tx_bps == pytest.approx(7500 * 8 / 60)

    async def test_a_duplicate_poll_is_not_an_infinite_rate(self):
        result = rate(previous_at=NOW, observed_at=NOW)
        assert result.measured is False
        assert result.reason == R.REASON_NON_POSITIVE_ELAPSED

    async def test_naive_timestamps_are_refused(self):
        result = rate(previous_at=datetime(2026, 9, 26, 11, 0, 0))
        assert result.measured is False
        assert result.reason == R.REASON_INVALID_TIMESTAMPS

    async def test_missing_speed_withholds_utilisation_but_keeps_the_rate(self):
        result = rate(current_in=1000.0 + 75_000, speed_mbps=None)
        assert result.rx_bps == pytest.approx(10_000.0)
        assert result.utilization_pct is None
        assert result.reason == R.REASON_NO_SPEED

    async def test_an_implausible_rate_is_not_published_as_utilisation(self):
        # ifSpeed saturates near 4.29 Gbps, so a real 10 Gbps port can look "over
        # capacity": the SPEED is the suspect value, so no percentage is
        # published even though the rate itself is real.
        result = rate(current_in=1000.0 + 75_000_000, speed_mbps=1.0)
        assert result.rx_bps == pytest.approx(10_000_000.0)
        assert result.utilization_pct is None
        assert result.reason == R.REASON_RATE_EXCEEDS_SPEED

    async def test_an_idle_interface_is_a_measured_zero_percent(self):
        result = rate()
        assert result.rx_bps == 0.0 and result.tx_bps == 0.0
        assert result.utilization_pct == 0.0
        assert result.reason is None


def _bundle(in_octets: float, out_octets: float):
    from app.collectors.base import (
        NormalizedBundle,
        NormalizedDevice,
        NormalizedInterface,
    )

    return NormalizedBundle(
        devices=[
            NormalizedDevice(
                external_id="rate-lab-sw", name="rate-lab-sw", device_type="switch"
            )
        ],
        interfaces=[
            NormalizedInterface(
                device_external_id="rate-lab-sw",
                external_id="1",
                name="Gi0/1",
                admin_status="up",
                oper_status="up",
                speed_mbps=100.0,
                in_octets=in_octets,
                out_octets=out_octets,
            )
        ],
        neighbors=[],
        neighbor_status={"lldp": "empty"},
    )


def _saver(db):
    from app.repositories.device_repository import DeviceRepository
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


async def _own_interface(db, tenant_id):
    """This test's interface, not an unrelated row already in the dev database."""
    from sqlalchemy import select

    from app.models.device import Device
    from app.models.interface import Interface

    device = (
        await db.execute(
            select(Device).where(
                Device.tenant_id == tenant_id, Device.external_id == "rate-lab-sw"
            )
        )
    ).scalars().first()
    assert device is not None, "the bundle's device should have been upserted"
    return (
        await db.execute(select(Interface).where(Interface.device_id == device.id))
    ).scalars().first()


class TestRatePersistence:
    """The existing save path turns two stored samples into stored rates."""

    async def test_first_sample_stores_counters_without_rates(
        self, tenant, db, monitoring_source_factory
    ):
        source = await monitoring_source_factory(
            source_type="snmp", hostname="127.0.0.1", port=1161, snmp_version="v2c"
        )
        saver = _saver(db)

        first = await saver.save(source, _bundle(1000.0, 2000.0))
        await db.commit()
        row = await _own_interface(db, tenant.id)
        assert row is not None
        # One sample: counters stored, rates honestly NULL, freshness recorded.
        assert row.in_octets == 1000.0 and row.out_octets == 2000.0
        assert row.rx_bps is None and row.tx_bps is None
        assert row.utilization_pct is None
        assert row.last_telemetry_at is not None
        assert first.rates_measured == 0

        # A real second sample 60 seconds later: +75000 in / +37500 out bytes.
        row.last_telemetry_at = datetime.now(timezone.utc) - timedelta(seconds=60)
        await db.commit()

        second = await saver.save(source, _bundle(76_000.0, 39_500.0))
        await db.commit()
        await db.refresh(row)

        assert second.rates_measured == 1
        assert row.rx_bps == pytest.approx(10_000.0, rel=0.05)
        assert row.tx_bps == pytest.approx(5_000.0, rel=0.05)
        assert row.utilization_pct == pytest.approx(0.01, rel=0.05)
        assert row.last_telemetry_at is not None

    async def test_a_counter_reset_stores_no_rate(
        self, tenant, db, monitoring_source_factory
    ):
        source = await monitoring_source_factory(
            source_type="snmp", hostname="127.0.0.1", port=1161, snmp_version="v2c"
        )
        saver = _saver(db)
        await saver.save(source, _bundle(5_000_000.0, 6_000_000.0))
        await db.commit()

        row = await _own_interface(db, tenant.id)
        assert row is not None
        row.last_telemetry_at = datetime.now(timezone.utc) - timedelta(seconds=60)
        await db.commit()

        # Device restarted: counters went backwards.
        second = await saver.save(source, _bundle(10.0, 20.0))
        await db.commit()
        await db.refresh(row)

        assert second.rates_measured == 0
        assert row.rx_bps is None and row.tx_bps is None
        assert row.utilization_pct is None
        # The counters themselves are stored exactly as reported.
        assert row.in_octets == 10.0 and row.out_octets == 20.0

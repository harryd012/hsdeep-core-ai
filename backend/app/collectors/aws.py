"""
Amazon Web Services (AWS) collector.

Discovers EC2 instances, VPCs, subnets, security groups and load balancers
via boto3, then normalizes them into the shared `NormalizedBundle` shape so
they flow through the same recognition / identity / topology pipeline as
every other resource.

Connection details from `MonitoringSourceConnectionInfo`:
    config["regions"]     -> list[str] AWS regions to inventory (preferred)
    config["region"]      -> single region (fallback when `regions` absent)
    username              -> AWS access key ID
    password              -> AWS secret access key (encrypted at rest)
    api_key               -> alternative: AWS session token (STS temporary creds)
    config["role_arn"]    -> optional: assume this role before inventorying

Dependency: boto3 (NOT currently pinned in requirements.txt - the import is
deferred so this module still loads, and `authenticate()` returns an honest
failure telling the operator to install it, rather than crashing the process).

boto3 is synchronous, so every call is dispatched with `asyncio.to_thread` to
keep the collection event loop responsive.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.collectors.base import (
    BaseCollector,
    CollectorConfigError,
    CollectorRegistry,
    ConnectionCheck,
    NormalizedBundle,
    NormalizedDevice,
)

logger = logging.getLogger(__name__)

#: boto3 paginators cap pages; EC2 is paged by the SDK.
_DEFAULT_REGIONS: tuple[str, ...] = ("us-east-1",)

#: AWS instance states that mean the instance is actually serving.
_RUNNING_STATES = frozenset({"running", "pending"})

#: AWS instance states that mean the instance is not serving.
_STOPPED_STATES = frozenset({
    "stopped", "stopping", "terminated", "shutting-down", "rebooting",
})

#: Elastic Load Balancing v2 states.
_LB_ACTIVE_STATES = frozenset({"active"})
_LB_FAILED_STATES = frozenset({"failed"})


def _state_status(
    value: Any,
    up_states: frozenset[str],
    down_states: frozenset[str],
) -> str:
    """Map a provider state string onto up/down, defaulting to `unknown`.

    An unrecognised state (e.g. ELB `provisioning`) must NOT be reported as
    `down` - it is simply unknown, and claiming an outage that was not observed
    would be dishonest.
    """
    state = str(value or "").strip().lower()
    if state in up_states:
        return "up"
    if state in down_states:
        return "down"
    return "unknown"


def _import_boto3() -> Any:
    """Deferred import so the module loads without boto3 installed."""
    try:
        import boto3  # type: ignore[import]
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise CollectorConfigError(
            "boto3 is required for the AWS collector. "
            "Install it via: pip install boto3"
        ) from exc
    return boto3


@CollectorRegistry.register("aws")
class AwsCollector(BaseCollector):
    display_name = "Amazon Web Services"
    implemented = True

    # -- config helpers -------------------------------------------------------

    def _regions(self) -> list[str]:
        """Configured regions, `regions` taking precedence over `region`."""
        cfg = self.source.config or {}
        regions = cfg.get("regions")
        if isinstance(regions, str):
            regions = [regions]
        if regions:
            return [str(r) for r in regions if r]
        if cfg.get("region"):
            return [str(cfg["region"])]
        return list(_DEFAULT_REGIONS)

    def _require_config(self) -> None:
        missing: list[str] = []
        if not self.source.username:
            missing.append("username (AWS access key ID)")
        if not self.source.password:
            missing.append("password (AWS secret access key)")
        # A session token alone cannot authenticate.
        if missing:
            raise CollectorConfigError(
                f"AWS config missing required field(s): {missing}"
            )

    def _session(self) -> Any:
        """Build a boto3 Session, optionally assuming a role."""
        # Validate config BEFORE importing boto3 so a missing credential is
        # reported as a config error even when the SDK is not installed.
        self._require_config()
        boto3 = _import_boto3()
        cfg = self.source.config or {}
        session = boto3.Session(
            aws_access_key_id=self.source.username,
            aws_secret_access_key=self.source.password,
            aws_session_token=self.source.api_key or None,
            region_name=self._regions()[0],
        )
        role_arn = cfg.get("role_arn")
        if role_arn:
            sts = session.client("sts")
            assumed = sts.assume_role(
                RoleArn=str(role_arn),
                RoleSessionName="hsdeep-discovery",
            )["Credentials"]
            session = boto3.Session(
                aws_access_key_id=assumed["AccessKeyId"],
                aws_secret_access_key=assumed["SecretAccessKey"],
                aws_session_token=assumed["SessionToken"],
                region_name=self._regions()[0],
            )
        return session

    # -- BaseCollector interface -----------------------------------------------

    async def authenticate(self) -> ConnectionCheck:
        """Verify credentials with STS, which needs no resource permissions."""
        try:
            self._require_config()
            session = self._session()
            identity = await asyncio.to_thread(
                lambda: session.client("sts").get_caller_identity()
            )
        except CollectorConfigError:
            raise
        except Exception as exc:  # noqa: BLE001
            return ConnectionCheck(
                success=False,
                message=f"AWS authentication failed: {type(exc).__name__}: {exc}",
            )
        account = identity.get("Account", "unknown")
        return ConnectionCheck(
            success=True,
            message=f"Connected to AWS account {account}.",
        )

    async def collect(self) -> dict[str, Any]:
        """Inventory every configured region. Best-effort per service/region.

        A failure for one service or region is logged and skipped rather than
        discarding the rest of the inventory.
        """
        session = self._session()
        raw: dict[str, Any] = {
            "regions": self._regions(),
            "instances": [],
            "vpcs": [],
            "subnets": [],
            "security_groups": [],
            "load_balancers": [],
            "errors": [],
        }

        for region in self._regions():
            await self._collect_region(session, region, raw)

        return raw

    async def _collect_region(
        self, session: Any, region: str, raw: dict[str, Any]
    ) -> None:
        """Inventory one region, tagging every row with its region/account."""

        async def _safe(label: str, fn: Any, bucket: str) -> None:
            """Run one service call; record the failure instead of raising."""
            try:
                rows = await asyncio.to_thread(fn)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "AWS collect(): %s failed: region=%s source_id=%s error_type=%s",
                    label, region, self.source.id, type(exc).__name__,
                )
                raw["errors"].append(f"{region}/{label}: {type(exc).__name__}")
                return
            for row in rows or []:
                row["_region"] = region
                raw[bucket].append(row)

        def _client(service: str) -> Any:
            return session.client(service, region_name=region)

        async def instances() -> list[dict[str, Any]]:
            ec2 = _client("ec2")
            out: list[dict[str, Any]] = []
            paginator = ec2.get_paginator("describe_instances")
            for page in paginator.paginate():
                # describe_instances nests instances inside reservations.
                for reservation in page.get("Reservations", []):
                    out.extend(reservation.get("Instances", []))
            return out

        async def vpcs() -> list[dict[str, Any]]:
            return _client("ec2").describe_vpcs().get("Vpcs", [])

        async def subnets() -> list[dict[str, Any]]:
            return _client("ec2").describe_subnets().get("Subnets", [])

        async def security_groups() -> list[dict[str, Any]]:
            return _client("ec2").describe_security_groups().get(
                "SecurityGroups", []
            )

        async def load_balancers() -> list[dict[str, Any]]:
            elbv2 = _client("elbv2")
            return elbv2.describe_load_balancers().get("LoadBalancers", [])

        await _safe("ec2:describe_instances", instances, "instances")
        await _safe("ec2:describe_vpcs", vpcs, "vpcs")
        await _safe("ec2:describe_subnets", subnets, "subnets")
        await _safe(
            "ec2:describe_security_groups", security_groups, "security_groups"
        )
        await _safe(
            "elbv2:describe_load_balancers", load_balancers, "load_balancers"
        )

    def normalize(self, raw: dict[str, Any]) -> NormalizedBundle:
        """Pure mapping: raw AWS API rows -> NormalizedBundle.

        `external_id` is prefixed with the provider and region
        (`aws:<region>:<kind>:<id>`) because a bare AWS resource ID such as
        `i-0abc123` is only unique within one region/account pair. Fields AWS
        does not return are left `None` rather than guessed.
        """
        devices: list[NormalizedDevice] = []

        for row in raw.get("instances", []):
            instance_id = row.get("InstanceId")
            if not instance_id:
                continue
            region = row.get("_region") or "unknown"
            state = ((row.get("State") or {}).get("Name") or "").lower()
            devices.append(
                NormalizedDevice(
                    external_id=f"aws:{region}:instance:{instance_id}",
                    name=_tag(row, "Name") or instance_id,
                    device_type="cloud_resource",
                    vendor="Amazon",
                    model=row.get("InstanceType"),
                    os_version=row.get("PlatformDetails"),
                    hostname=row.get("PrivateDnsName"),
                    ip_address=row.get("PrivateIpAddress")
                    or row.get("PublicIpAddress"),
                    status=_state_status(state, _RUNNING_STATES, _STOPPED_STATES),
                    site_hint=region,
                    custom_metadata={
                        "provider": "aws",
                        "resource_kind": "ec2_instance",
                        "region": region,
                        "availability_zone": (row.get("Placement") or {}).get(
                            "AvailabilityZone"
                        ),
                        "vpc_id": row.get("VpcId"),
                        "subnet_id": row.get("SubnetId"),
                        "image_id": row.get("ImageId"),
                        "state": state or None,
                    },
                )
            )

        for row in raw.get("vpcs", []):
            vpc_id = row.get("VpcId")
            if not vpc_id:
                continue
            region = row.get("_region") or "unknown"
            devices.append(
                NormalizedDevice(
                    external_id=f"aws:{region}:vpc:{vpc_id}",
                    name=_tag(row, "Name") or vpc_id,
                    device_type="cloud_resource",
                    vendor="Amazon",
                    status=(
                        "up"
                        if (row.get("State") or "available") == "available"
                        else "unknown"
                    ),
                    site_hint=region,
                    custom_metadata={
                        "provider": "aws",
                        "resource_kind": "vpc",
                        "region": region,
                        "cidr_block": row.get("CidrBlock"),
                        "is_default": row.get("IsDefault"),
                    },
                )
            )

        for row in raw.get("subnets", []):
            subnet_id = row.get("SubnetId")
            if not subnet_id:
                continue
            region = row.get("_region") or "unknown"
            devices.append(
                NormalizedDevice(
                    external_id=f"aws:{region}:subnet:{subnet_id}",
                    name=_tag(row, "Name") or subnet_id,
                    device_type="cloud_resource",
                    vendor="Amazon",
                    status=(
                        "up"
                        if row.get("State", "available") == "available"
                        else "unknown"
                    ),
                    site_hint=region,
                    custom_metadata={
                        "provider": "aws",
                        "resource_kind": "subnet",
                        "region": region,
                        "cidr_block": row.get("CidrBlock"),
                        "availability_zone": row.get("AvailabilityZone"),
                        "vpc_id": row.get("VpcId"),
                    },
                )
            )

        for row in raw.get("security_groups", []):
            group_id = row.get("GroupId")
            if not group_id:
                continue
            region = row.get("_region") or "unknown"
            devices.append(
                NormalizedDevice(
                    external_id=f"aws:{region}:security_group:{group_id}",
                    name=row.get("GroupName") or group_id,
                    device_type="cloud_resource",
                    vendor="Amazon",
                    status="up",
                    site_hint=region,
                    custom_metadata={
                        "provider": "aws",
                        "resource_kind": "security_group",
                        "region": region,
                        "vpc_id": row.get("VpcId"),
                        "description": row.get("Description"),
                        "rule_count": len(row.get("IpPermissions") or []),
                    },
                )
            )

        for row in raw.get("load_balancers", []):
            name = row.get("LoadBalancerName")
            if not name:
                continue
            region = row.get("_region") or "unknown"
            state = ((row.get("State") or {}).get("Code") or "").lower()
            devices.append(
                NormalizedDevice(
                    external_id=f"aws:{region}:load_balancer:{name}",
                    name=name,
                    device_type="cloud_resource",
                    vendor="Amazon",
                    hostname=row.get("DNSName"),
                    status=_state_status(
                        state, _LB_ACTIVE_STATES, _LB_FAILED_STATES
                    ),
                    site_hint=region,
                    custom_metadata={
                        "provider": "aws",
                        "resource_kind": "load_balancer",
                        "region": region,
                        "scheme": row.get("Scheme"),
                        "type": row.get("Type"),
                        "vpc_id": row.get("VpcId"),
                        "state": state or None,
                    },
                )
            )

        return NormalizedBundle(devices=devices)


def _tag(row: dict[str, Any], key: str) -> str | None:
    """Read one tag value (EC2 returns tags as a `[{Key, Value}]` list)."""
    for tag in row.get("Tags") or []:
        if tag.get("Key") == key:
            return tag.get("Value")
    return None

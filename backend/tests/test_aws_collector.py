"""
Unit tests for AwsCollector.normalize() and its config validation.

Pure, no I/O and no AWS credentials: `normalize()` is a function over plain
dicts, so fixtures are the only "AWS" needed. Response shapes mirror real
boto3 output (EC2 `describe_instances` nests instances inside `Reservations`,
security rules live under `IpPermissions`, load balancer state under
`State.Code`, tags under `Tags[{Key,Value}]`).
"""
from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from app.collectors.aws import AwsCollector, _tag
from app.collectors.base import CollectorConfigError, NormalizedBundle

_EMPTY_RAW = {
    "regions": ["eu-west-1"],
    "instances": [],
    "vpcs": [],
    "subnets": [],
    "security_groups": [],
    "load_balancers": [],
    "errors": [],
}

_INSTANCES_RAW = {
    "regions": ["eu-west-1"],
    "instances": [
        {
            "InstanceId": "i-0abc123",
            "InstanceType": "t3.micro",
            "State": {"Name": "running"},
            "PrivateIpAddress": "10.0.1.10",
            "PrivateDnsName": "ip-10-0-1-10.eu-west-1.compute.internal",
            "PlatformDetails": "Linux/UNIX",
            "VpcId": "vpc-111",
            "SubnetId": "subnet-222",
            "ImageId": "ami-999",
            "Placement": {"AvailabilityZone": "eu-west-1a"},
            "Tags": [{"Key": "Name", "Value": "web-01"}],
            "_region": "eu-west-1",
        },
        {
            "InstanceId": "i-0def456",
            "InstanceType": "m5.large",
            "State": {"Name": "stopped"},
            # No tags, no OS, no AZ - normalize must not guess these.
            "_region": "eu-west-1",
        },
    ],
    "vpcs": [
        {
            "VpcId": "vpc-111",
            "CidrBlock": "10.0.0.0/16",
            "State": "available",
            "IsDefault": False,
            "Tags": [{"Key": "Name", "Value": "prod-vpc"}],
            "_region": "eu-west-1",
        }
    ],
    "subnets": [
        {
            "SubnetId": "subnet-222",
            "VpcId": "vpc-111",
            "CidrBlock": "10.0.1.0/24",
            "AvailabilityZone": "eu-west-1a",
            "State": "available",
            "_region": "eu-west-1",
        }
    ],
    "security_groups": [
        {
            "GroupId": "sg-333",
            "GroupName": "web-sg",
            "VpcId": "vpc-111",
            "Description": "web ingress",
            "IpPermissions": [{"FromPort": 443}, {"FromPort": 80}],
            "_region": "eu-west-1",
        }
    ],
    "load_balancers": [
        {
            "LoadBalancerName": "alb-web",
            "DNSName": "alb-web-123.eu-west-1.elb.amazonaws.com",
            "Scheme": "internet-facing",
            "Type": "application",
            "VpcId": "vpc-111",
            "State": {"Code": "active"},
            "_region": "eu-west-1",
        },
        {
            "LoadBalancerName": "alb-broken",
            "State": {"Code": "provisioning"},
            "_region": "eu-west-1",
        },
    ],
    "errors": [],
}


def _source(config=None, username="AKIAEXAMPLE", password="secret"):
    """Stand-in for MonitoringSourceConnectionInfo (secret never asserted)."""
    return SimpleNamespace(
        id=None,
        tenant_id=None,
        name="aws-test",
        source_type="aws",
        hostname=None,
        ip_address=None,
        port=None,
        protocol=None,
        snmp_version=None,
        username=username,
        api_key=None,
        password=password,
        config=config or {},
        verify_ssl=True,
    )


class TestAwsNormalize:

    def test_empty_payload_returns_empty_bundle(self):
        bundle = AwsCollector(source=None).normalize(_EMPTY_RAW)
        assert isinstance(bundle, NormalizedBundle)
        assert bundle.devices == []

    def test_instance_maps_core_fields(self):
        bundle = AwsCollector(source=None).normalize(_INSTANCES_RAW)
        web = {d.name: d for d in bundle.devices}["web-01"]

        assert web.external_id == "aws:eu-west-1:instance:i-0abc123"
        assert web.device_type == "cloud_resource"
        assert web.vendor == "Amazon"
        assert web.model == "t3.micro"
        assert web.os_version == "Linux/UNIX"
        assert web.ip_address == "10.0.1.10"
        assert web.status == "up"
        assert web.site_hint == "eu-west-1"
        assert web.custom_metadata["availability_zone"] == "eu-west-1a"
        assert web.custom_metadata["vpc_id"] == "vpc-111"

    def test_stopped_instance_maps_to_down(self):
        bundle = AwsCollector(source=None).normalize(_INSTANCES_RAW)
        assert {d.name: d for d in bundle.devices}["i-0def456"].status == "down"

    def test_untagged_instance_leaves_absent_fields_none(self):
        """Absent AWS fields must stay None, never be invented."""
        bundle = AwsCollector(source=None).normalize(_INSTANCES_RAW)
        untagged = next(d for d in bundle.devices if d.name == "i-0def456")
        assert untagged.model == "m5.large"
        assert untagged.os_version is None
        assert untagged.ip_address is None
        assert untagged.hostname is None
        assert untagged.custom_metadata["availability_zone"] is None

    def test_external_id_is_region_scoped(self):
        """A bare instance id is not unique across regions, so it is prefixed."""
        bundle = AwsCollector(source=None).normalize(_INSTANCES_RAW)
        instance = next(d for d in bundle.devices if d.name == "web-01")
        assert instance.external_id.startswith("aws:eu-west-1:instance:")

    def test_vpc_subnet_and_security_group_are_emitted(self):
        bundle = AwsCollector(source=None).normalize(_INSTANCES_RAW)
        by_id = {d.external_id: d for d in bundle.devices}

        vpc = by_id["aws:eu-west-1:vpc:vpc-111"]
        assert vpc.name == "prod-vpc"
        assert vpc.status == "up"
        assert vpc.custom_metadata["cidr_block"] == "10.0.0.0/16"

        subnet = by_id["aws:eu-west-1:subnet:subnet-222"]
        assert subnet.name == "subnet-222"  # no Name tag -> id
        assert subnet.custom_metadata["availability_zone"] == "eu-west-1a"

        sg = by_id["aws:eu-west-1:security_group:sg-333"]
        assert sg.name == "web-sg"
        assert sg.custom_metadata["rule_count"] == 2

    def test_load_balancer_status_from_state_code(self):
        bundle = AwsCollector(source=None).normalize(_INSTANCES_RAW)
        by_id = {d.external_id: d for d in bundle.devices}
        assert by_id["aws:eu-west-1:load_balancer:alb-web"].status == "up"
        # "provisioning" is neither active nor failed -> honest "unknown"
        assert by_id["aws:eu-west-1:load_balancer:alb-broken"].status == "unknown"

    def test_rows_without_identity_are_skipped_not_invented(self):
        raw = {
            "instances": [{"InstanceType": "t3.micro"}],  # no InstanceId
            "vpcs": [{"CidrBlock": "10.0.0.0/16"}],       # no VpcId
            "subnets": [{"CidrBlock": "10.0.1.0/24"}],    # no SubnetId
            "security_groups": [{"GroupName": "x"}],      # no GroupId
            "load_balancers": [{"DNSName": "x.elb"}],     # no name
        }
        assert AwsCollector(source=None).normalize(raw).devices == []

    def test_normalize_is_pure(self):
        collector = AwsCollector(source=None)
        first = collector.normalize(_INSTANCES_RAW)
        second = collector.normalize(_INSTANCES_RAW)
        assert [d.external_id for d in first.devices] == [
            d.external_id for d in second.devices
        ]

    def test_normalize_does_not_mutate_input(self):
        raw = copy.deepcopy(_INSTANCES_RAW)
        AwsCollector(source=None).normalize(raw)
        assert raw == _INSTANCES_RAW


class TestAwsTagHelper:

    def test_reads_matching_tag(self):
        row = {"Tags": [{"Key": "Name", "Value": "web"},
                        {"Key": "env", "Value": "prod"}]}
        assert _tag(row, "Name") == "web"
        assert _tag(row, "env") == "prod"

    def test_missing_tag_and_missing_tags_return_none(self):
        assert _tag({"Tags": [{"Key": "env", "Value": "prod"}]}, "Name") is None
        assert _tag({}, "Name") is None


class TestAwsConfig:

    def test_missing_credentials_raise_config_error(self):
        collector = AwsCollector(source=_source(username=None, password=None))
        with pytest.raises(CollectorConfigError) as exc:
            collector._require_config()
        assert "access key ID" in str(exc.value)

    def test_regions_prefers_list_over_single_region(self):
        collector = AwsCollector(
            source=_source(
                config={"regions": ["eu-west-1", "us-east-1"],
                        "region": "ap-south-1"}
            )
        )
        assert collector._regions() == ["eu-west-1", "us-east-1"]

    def test_single_region_string_is_accepted(self):
        collector = AwsCollector(source=_source(config={"regions": "eu-west-1"}))
        assert collector._regions() == ["eu-west-1"]

    def test_defaults_to_us_east_1_when_unconfigured(self):
        assert AwsCollector(source=_source())._regions() == ["us-east-1"]

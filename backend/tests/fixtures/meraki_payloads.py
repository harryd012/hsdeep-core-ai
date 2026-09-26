"""
Test fixtures for Meraki collector normalization.

These represent real Cisco Meraki Dashboard API responses (simplified for test readability).
Documented at https://developer.cisco.com/meraki/api/
"""

# Meraki organizations list (for authenticate verification)
MERAKI_ORGANIZATIONS = [
    {
        "id": "111111",
        "name": "Demo Org",
        "url": "https://dashboard.meraki.com/o/111111/manage/organization/overview",
    },
    {
        "id": "222222",
        "name": "Production Org",
        "url": "https://dashboard.meraki.com/o/222222/manage/organization/overview",
    }
]

# Meraki networks for an organization
MERAKI_NETWORKS_SINGLE = [
    {
        "id": "L_111111",
        "organizationId": "111111",
        "name": "Main Office",
        "productTypes": ["switch"],
        "type": "switch",
        "timeZone": "America/Los_Angeles",
    }
]

MERAKI_NETWORKS_MULTI = [
    {
        "id": "L_111111",
        "organizationId": "111111",
        "name": "Main Office",
        "productTypes": ["switch"],
        "type": "switch",
    },
    {
        "id": "L_222222",
        "organizationId": "111111",
        "name": "Branch Office",
        "productTypes": ["wireless"],
        "type": "wireless",
    },
    {
        "id": "L_333333",
        "organizationId": "111111",
        "name": "Management",
        "productTypes": ["appliance"],
        "type": "appliance",
    }
]

# Meraki devices for a network (switches)
MERAKI_DEVICES_SWITCH = [
    {
        "serial": "Q2QQ-XXXX-XXXX",
        "mac": "00:11:22:33:44:55",
        "networkId": "L_111111",
        "name": "Switch-Main-1",
        "model": "MS225-48",
        "type": "switch",
        "status": "online",
        "url": "https://dashboard.meraki.com/o/111111/manage/networks/L_111111/devices/Q2QQ-XXXX-XXXX",
    },
    {
        "serial": "Q2QQ-YYYY-YYYY",
        "mac": "00:11:22:33:44:66",
        "networkId": "L_111111",
        "name": "Switch-Main-2",
        "model": "MS225-48",
        "type": "switch",
        "status": "online",
    }
]

# Meraki devices for a network (wireless APs)
MERAKI_DEVICES_WIRELESS = [
    {
        "serial": "Q2CD-XXXX-XXXX",
        "mac": "00:11:22:33:55:55",
        "networkId": "L_222222",
        "name": "AP-Branch-1",
        "model": "MR42",
        "type": "wireless",
        "status": "online",
    },
    {
        "serial": "Q2CD-YYYY-YYYY",
        "mac": "00:11:22:33:55:66",
        "networkId": "L_222222",
        "name": "AP-Branch-2",
        "model": "MR42",
        "type": "wireless",
        "status": "offline",
    }
]

# Meraki device statuses (aggregated status for a device)
MERAKI_DEVICE_STATUS_ONLINE = {
    "serial": "Q2QQ-XXXX-XXXX",
    "status": "online",
    "lastReportedAt": "2024-01-01T12:00:00Z",
    "publicIp": "203.0.113.1",
}

MERAKI_DEVICE_STATUS_OFFLINE = {
    "serial": "Q2CD-YYYY-YYYY",
    "status": "offline",
    "lastReportedAt": "2024-01-01T11:30:00Z",
}

# Meraki complete collect() response (single network, switches)
MERAKI_COLLECT_SINGLE = {
    "organizations": MERAKI_ORGANIZATIONS,
    "networks": MERAKI_NETWORKS_SINGLE,
    "devices": MERAKI_DEVICES_SWITCH,
    "device_statuses": {
        "Q2QQ-XXXX-XXXX": MERAKI_DEVICE_STATUS_ONLINE,
        "Q2QQ-YYYY-YYYY": MERAKI_DEVICE_STATUS_ONLINE,
    }
}

# Meraki complete collect() response (multiple networks/device types)
MERAKI_COLLECT_MULTI = {
    "organizations": MERAKI_ORGANIZATIONS,
    "networks": MERAKI_NETWORKS_MULTI,
    "devices": MERAKI_DEVICES_SWITCH + MERAKI_DEVICES_WIRELESS,
    "device_statuses": {
        "Q2QQ-XXXX-XXXX": MERAKI_DEVICE_STATUS_ONLINE,
        "Q2QQ-YYYY-YYYY": MERAKI_DEVICE_STATUS_ONLINE,
        "Q2CD-XXXX-XXXX": MERAKI_DEVICE_STATUS_ONLINE,
        "Q2CD-YYYY-YYYY": MERAKI_DEVICE_STATUS_OFFLINE,
    }
}

# Meraki complete collect() response (empty)
MERAKI_COLLECT_EMPTY = {
    "organizations": [],
    "networks": [],
    "devices": [],
    "device_statuses": {}
}

# Meraki device with sensors (e.g., temperature, humidity for certain devices)
MERAKI_DEVICE_WITH_SENSORS = {
    "serial": "Q2QQ-XXXX-XXXX",
    "mac": "00:11:22:33:44:55",
    "name": "Switch-with-sensors",
    "model": "MS225-48",
    "type": "switch",
    "status": "online",
    "temperature": 38,
    "humidity": 45,
}

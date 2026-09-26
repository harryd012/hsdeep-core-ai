"""
Test fixtures for PRTG collector normalization.

These represent real PRTG API responses (simplified for test readability).
"""

# Minimal PRTG status.json response (for authenticate verification)
PRTG_STATUS_EMPTY = {
    "status": 200,
    "version": "24.2.86.1823",
    "clock": 1234567890,
}

# Groups list (used for site_hint resolution)
PRTG_GROUPS = [
    {"objid": "1", "name": "Local Network", "parentid": "-1"},
    {"objid": "2", "name": "Cloud", "parentid": "-1"},
    {"objid": "3", "name": "Servers", "parentid": "1"},
]

# Single device with one sensor
PRTG_DEVICES_SINGLE = [
    {
        "objid": "10001",
        "name": "Router-Core-1",
        "host": "192.168.1.1",
        "status": "Up",
        "parentid": "-1",
        "group": "1",  # Group ID (not name) - resolves via groups_by_id lookup
    }
]

PRTG_SENSORS_SINGLE = [
    {
        "objid": "20001",
        "name": "CPU",
        "status": "3",  # up
        "status_raw": 3,
        "lastvalue_": "45.2",
        "device": "Router-Core-1",
        "parentid": "10001",
    }
]

# Channels/metrics for CPU sensor
PRTG_CHANNELS_SINGLE = {
    "20001": [  # sensor objid
        {
            "objid": "0",
            "name": "CPU",
            "lastvalue_": "45.2",
            "lasttime": "2024-01-01 12:00:00",
            "unit": "%",
        }
    ]
}

# Historic data for the sensor
PRTG_HISTORIC_SINGLE = {
    "20001": [
        {
            "datetime": "2024-01-01 11:00:00",
            "value": "42.0",
        },
        {
            "datetime": "2024-01-01 12:00:00",
            "value": "45.2",
        },
    ]
}

# Multiple devices with interfaces/sensors
PRTG_DEVICES_MULTI = [
    {
        "objid": "10001",
        "name": "Switch-1",
        "host": "192.168.1.10",
        "status": "Up",
        "parentid": "-1",
        "group": "1",
    },
    {
        "objid": "10002",
        "name": "Firewall-1",
        "host": "192.168.1.254",
        "status": "Up",
        "parentid": "-1",
        "group": "1",
    },
]

PRTG_SENSORS_MULTI = [
    {
        "objid": "20001",
        "name": "Eth0/0",
        "status": "3",
        "status_raw": 3,
        "lastvalue_": "1000000",
        "device": "Switch-1",
        "parentid": "10001",
    },
    {
        "objid": "20002",
        "name": "Eth0/1",
        "status": "3",
        "status_raw": 3,
        "lastvalue_": "500000",
        "device": "Switch-1",
        "parentid": "10001",
    },
    {
        "objid": "20003",
        "name": "CPU",
        "status": "3",
        "status_raw": 3,
        "lastvalue_": "55.0",
        "device": "Firewall-1",
        "parentid": "10002",
    },
]

# Complete PRTG collect() response (empty - just structure)
PRTG_COLLECT_EMPTY = {
    "status": {},
    "groups": [],
    "devices": [],
    "sensors": [],
    "channels": {},
    "historic": {},
    "messages": [],
}

# Complete PRTG collect() response (single device)
PRTG_COLLECT_SINGLE = {
    "status": PRTG_STATUS_EMPTY,
    "groups": PRTG_GROUPS,
    "devices": PRTG_DEVICES_SINGLE,
    "sensors": PRTG_SENSORS_SINGLE,
    "channels": PRTG_CHANNELS_SINGLE,
    "historic": PRTG_HISTORIC_SINGLE,
    "messages": [],
}

# Complete PRTG collect() response (multiple devices)
PRTG_COLLECT_MULTI = {
    "status": PRTG_STATUS_EMPTY,
    "groups": PRTG_GROUPS,
    "devices": PRTG_DEVICES_MULTI,
    "sensors": PRTG_SENSORS_MULTI,
    "channels": {},
    "historic": {},
    "messages": [],
}

# Vendor-native alarm signal (PRTG message)
PRTG_MESSAGE_WARNING = {
    "objid": "40001",
    "name": "Sensor Down",
    "type": "down",
    "datetime": "2024-01-01 11:30:00",
    "status": "Down",
    "message": "Sensor 20001 (CPU) is down.",
    "parent": "Router-Core-1",  # Device name (used for name/host -> objid lookup)
}

PRTG_COLLECT_WITH_ALARM = {
    "status": PRTG_STATUS_EMPTY,
    "groups": PRTG_GROUPS,
    "devices": PRTG_DEVICES_SINGLE,
    "sensors": PRTG_SENSORS_SINGLE,
    "channels": PRTG_CHANNELS_SINGLE,
    "historic": PRTG_HISTORIC_SINGLE,
    "messages": [PRTG_MESSAGE_WARNING],
}

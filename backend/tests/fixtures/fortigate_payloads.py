"""
Test fixtures for FortiGate collector normalization.

These represent real FortiGate REST API responses (simplified for test readability).
"""

# FortiGate system/status response (for authenticate verification)
FORTIGATE_SYSTEM_STATUS = {
    "http_status": 200,
    "version": "v7.0.8",
    "results": {
        "current_admins": 1,
        "hostname": "FortiGate-100D",
        "system_mode": "NAT",
        "internal_switch_mode": "switch",
        "usb_modem": "notpresent",
        "management_vdom": "root",
    }
}

# FortiGate system/interface response (single interface)
FORTIGATE_INTERFACES_SINGLE = {
    "http_status": 200,
    "results": [
        {
            "name": "port1",
            "vdom": "root",
            "type": "physical",
            "ip": "192.168.1.1",
            "netmask": "255.255.255.0",
            "mac": "00:11:22:33:44:55",
            "mtu": 1500,
            "status": "up",
            "speed": "1000",
            "duplex": "full",
        },
        {
            "name": "port2",
            "vdom": "root",
            "type": "physical",
            "ip": "0.0.0.0",
            "netmask": "0.0.0.0",
            "mac": "00:11:22:33:44:56",
            "mtu": 1500,
            "status": "down",
            "speed": "0",
            "duplex": "full",
        }
    ]
}

# FortiGate system/interface response (multiple interfaces)
FORTIGATE_INTERFACES_MULTI = {
    "http_status": 200,
    "results": [
        {
            "name": "port1",
            "vdom": "root",
            "type": "physical",
            "ip": "192.168.1.1",
            "netmask": "255.255.255.0",
            "mac": "00:11:22:33:44:55",
            "status": "up",
            "speed": "1000",
        },
        {
            "name": "port2",
            "vdom": "root",
            "type": "physical",
            "ip": "10.0.0.1",
            "netmask": "255.255.255.0",
            "mac": "00:11:22:33:44:56",
            "status": "up",
            "speed": "1000",
        },
        {
            "name": "port3",
            "vdom": "root",
            "type": "physical",
            "ip": "0.0.0.0",
            "netmask": "0.0.0.0",
            "mac": "00:11:22:33:44:57",
            "status": "down",
            "speed": "0",
        },
        {
            "name": "vlan100",
            "vdom": "root",
            "type": "vlan",
            "ip": "172.16.0.1",
            "netmask": "255.255.255.0",
            "status": "up",
        }
    ]
}

# FortiGate system/status response (empty, for error handling)
FORTIGATE_SYSTEM_STATUS_ERROR = {
    "http_status": 401,
    "error": "Unauthorized"
}

# FortiGate interface with statistics/counters
FORTIGATE_INTERFACE_STATS = {
    "name": "port1",
    "vdom": "root",
    "type": "physical",
    "ip": "192.168.1.1",
    "netmask": "255.255.255.0",
    "mac": "00:11:22:33:44:55",
    "status": "up",
    "speed": "1000",
    "duplex": "full",
    "rx_packets": 1000000,
    "rx_bytes": 5000000000,
    "rx_errors": 100,
    "tx_packets": 900000,
    "tx_bytes": 4500000000,
    "tx_errors": 50,
}

# Complete FortiGate collect() response (single interface)
FORTIGATE_COLLECT_SINGLE = {
    "status": FORTIGATE_SYSTEM_STATUS,
    "interfaces": FORTIGATE_INTERFACES_SINGLE,
}

# Complete FortiGate collect() response (multiple interfaces)
FORTIGATE_COLLECT_MULTI = {
    "status": FORTIGATE_SYSTEM_STATUS,
    "interfaces": FORTIGATE_INTERFACES_MULTI,
}

# Complete FortiGate collect() response (empty)
FORTIGATE_COLLECT_EMPTY = {
    "status": FORTIGATE_SYSTEM_STATUS,
    "interfaces": {"http_status": 200, "results": []},
}

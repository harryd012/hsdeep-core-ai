"""
Test fixtures for Zabbix collector normalization.

These represent real Zabbix JSON-RPC API responses (simplified for test readability).
Documented at https://www.zabbix.com/documentation/current/en/manual/api
"""

# Zabbix host.get response (single host with interfaces)
ZABBIX_HOST_SINGLE = [
    {
        "hostid": "10001",
        "host": "web-server-01",
        "name": "Web Server 01",
        "status": "0",  # 0=monitored, 1=unmonitored
        "groups": [
            {"groupid": "1", "name": "Linux servers"},
            {"groupid": "2", "name": "Production"},
        ],
        "interfaces": [
            {
                "interfaceid": "20001",
                "type": "1",  # Agent
                "ip": "192.168.1.10",
                "port": "10050",
                "main": "1",
            }
        ],
    }
]

# Zabbix host.get response (multiple hosts)
ZABBIX_HOST_MULTI = [
    {
        "hostid": "10001",
        "host": "web-server-01",
        "name": "Web Server 01",
        "status": "0",
        "groups": [{"groupid": "1", "name": "Linux servers"}],
        "interfaces": [
            {
                "interfaceid": "20001",
                "type": "1",
                "ip": "192.168.1.10",
                "port": "10050",
                "main": "1",
            }
        ],
    },
    {
        "hostid": "10002",
        "host": "db-server-01",
        "name": "DB Server 01",
        "status": "0",
        "groups": [{"groupid": "1", "name": "Linux servers"}],
        "interfaces": [
            {
                "interfaceid": "20002",
                "type": "1",
                "ip": "192.168.1.20",
                "port": "10050",
                "main": "1",
            },
            {
                "interfaceid": "20003",
                "type": "2",  # SNMP
                "ip": "192.168.1.20",
                "port": "161",
                "main": "0",
            },
        ],
    },
    {
        "hostid": "10003",
        "host": "monitor-host",
        "name": "Monitor Host",
        "status": "1",  # Unmonitored
        "groups": [{"groupid": "3", "name": "Test hosts"}],
        "interfaces": [
            {
                "interfaceid": "20004",
                "type": "1",
                "ip": "192.168.1.30",
                "port": "10050",
                "main": "1",
            }
        ],
    },
]

# Zabbix item.get response (CPU/memory/disk items)
ZABBIX_ITEMS_SINGLE = [
    {
        "itemid": "30001",
        "key_": "system.cpu.load[all,avg1]",
        "name": "CPU load (1 min average)",
        "type": "0",  # Zabbix agent
        "value_type": "0",  # Numeric
        "lastvalue": "2.5",
        "units": "",
        "hostid": "10001",
    },
    {
        "itemid": "30002",
        "key_": "vm.memory.size[available]",
        "name": "Available memory",
        "type": "0",
        "value_type": "0",
        "lastvalue": "4294967296",
        "units": "B",
        "hostid": "10001",
    },
    {
        "itemid": "30003",
        "key_": "vfs.fs.size[/,used]",
        "name": "Used disk space on /",
        "type": "0",
        "value_type": "0",
        "lastvalue": "10737418240",
        "units": "B",
        "hostid": "10001",
    },
    {
        "itemid": "30004",
        "key_": "net.if.in[eth0,bytes]",
        "name": "Incoming traffic on eth0",
        "type": "0",
        "value_type": "3",  # Numeric unsigned
        "lastvalue": "1500000",
        "units": "Bps",
        "hostid": "10001",
    },
]

# Zabbix trigger.get response
ZABBIX_TRIGGERS = [
    {
        "triggerid": "40001",
        "description": "CPU load too high on Web Server 01",
        "priority": "4",  # High
        "state": "0",
        "value": "1",  # Problem
        "hostid": "10001",
    },
    {
        "triggerid": "40002",
        "description": "Disk space critically low on Web Server 01",
        "priority": "5",  # Disaster
        "state": "0",
        "value": "1",
        "hostid": "10001",
    },
]

# Zabbix problem.get response (active problems)
ZABBIX_PROBLEMS = [
    {
        "eventid": "50001",
        "name": "CPU load too high on Web Server 01",
        "severity": "4",  # High
        "status": "0",  # Active
    },
    {
        "eventid": "50002",
        "name": "Disk space critically low on Web Server 01",
        "severity": "5",  # Disaster
        "status": "0",
    },
]

# Zabbix hostgroup.get response
ZABBIX_HOST_GROUPS = [
    {"groupid": "1", "name": "Linux servers"},
    {"groupid": "2", "name": "Production"},
    {"groupid": "3", "name": "Test hosts"},
]

# Complete Zabbix collect() response (single host)
ZABBIX_COLLECT_SINGLE = {
    "host_groups": ZABBIX_HOST_GROUPS,
    "hosts": ZABBIX_HOST_SINGLE,
    "items": ZABBIX_ITEMS_SINGLE,
    "triggers": ZABBIX_TRIGGERS,
    "problems": ZABBIX_PROBLEMS,
}

# Complete Zabbix collect() response (multiple hosts)
ZABBIX_COLLECT_MULTI = {
    "host_groups": ZABBIX_HOST_GROUPS,
    "hosts": ZABBIX_HOST_MULTI,
    "items": ZABBIX_ITEMS_SINGLE,
    "triggers": ZABBIX_TRIGGERS,
    "problems": ZABBIX_PROBLEMS,
}

# Complete Zabbix collect() response (empty)
ZABBIX_COLLECT_EMPTY = {
    "host_groups": [],
    "hosts": [],
    "items": [],
    "triggers": [],
    "problems": [],
}
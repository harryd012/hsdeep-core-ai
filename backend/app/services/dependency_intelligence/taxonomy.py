"""
Deterministic protocol/service taxonomy and its dependency edges.

Purely declarative code - no database, no migration, no vendor-specific
infrastructure. A port is only set where a port genuinely applies (an L2 protocol
such as VLAN has no port and must not pretend to).

Dependency direction: ``depends_on`` lists what the protocol REQUIRES to work.
The graph built from this lives in ``graph.py``.
"""
from __future__ import annotations

from dataclasses import dataclass

NETWORK = "network"
TRANSPORT = "transport"
CORE = "core_service"
REMOTE = "remote_access_web"
AUTH = "authentication"
LOGGING = "logging_mail"
ROUTING = "routing"
VPN = "tunneling_vpn"
SWITCHING = "switching"
WIRELESS = "wireless"

CATEGORIES: tuple[str, ...] = (
    NETWORK, TRANSPORT, CORE, REMOTE, AUTH, LOGGING, ROUTING, VPN, SWITCHING, WIRELESS,
)


@dataclass(frozen=True)
class Protocol:
    """One protocol or service in the taxonomy."""

    identifier: str
    display_name: str
    category: str
    dependencies: tuple[str, ...] = ()
    optional_dependencies: tuple[str, ...] = ()
    transport: str | None = None
    port: int | None = None
    description: str = ""

    def to_dict(self) -> dict:
        return {
            "identifier": self.identifier,
            "display_name": self.display_name,
            "category": self.category,
            "dependencies": list(self.dependencies),
            "optional_dependencies": list(self.optional_dependencies),
            "transport": self.transport,
            "port": self.port,
            "description": self.description,
        }


def _p(id_: str, name: str, cat: str, deps: tuple[str, ...] = (), **kw) -> Protocol:
    return Protocol(identifier=id_, display_name=name, category=cat, dependencies=deps, **kw)


_PROTOCOLS: tuple[Protocol, ...] = (
    # NETWORK (L3 primitives - nothing below them in this taxonomy)
    _p("ipv4", "IPv4", NETWORK),
    _p("ipv6", "IPv6", NETWORK),
    _p("icmp", "ICMP", NETWORK, ("ipv4",), description="IPv4 reachability probing."),
    _p("icmpv6", "ICMPv6", NETWORK, ("ipv6",), description="IPv6 reachability and NDP transport."),
    _p("arp", "ARP", NETWORK, description="IPv4 address resolution on the local segment."),
    _p("ndp", "NDP", NETWORK, ("icmpv6",), description="IPv6 neighbour discovery."),
    # TRANSPORT - IPv4 is the canonical underlay; IPv6 is an optional alternative.
    _p("tcp", "TCP", TRANSPORT, ("ipv4",), optional_dependencies=("ipv6",)),
    _p("udp", "UDP", TRANSPORT, ("ipv4",), optional_dependencies=("ipv6",)),
    _p("quic", "QUIC", TRANSPORT, ("udp",), port=443),
    # CORE SERVICES
    _p("dns", "DNS", CORE, ("udp",), transport="udp", port=53),
    _p("dhcp", "DHCP", CORE, ("udp",), transport="udp", port=67),
    _p("dhcpv6", "DHCPv6", CORE, ("udp",), transport="udp", port=547),
    _p("ntp", "NTP", CORE, ("udp",), transport="udp", port=123),
    _p("snmp", "SNMP", CORE, ("udp",), transport="udp", port=161,
       description="Monitoring polling/telemetry over UDP/161."),
    # REMOTE ACCESS / WEB
    _p("ssh", "SSH", REMOTE, ("tcp",), transport="tcp", port=22),
    _p("http", "HTTP", REMOTE, ("tcp",), transport="tcp", port=80),
    _p("tls", "TLS", REMOTE, ("tcp",), transport="tcp", port=443),
    _p("https", "HTTPS", REMOTE, ("tls",), transport="tcp", port=443),
    # AUTHENTICATION
    _p("ldap", "LDAP", AUTH, ("tcp",), transport="tcp", port=389),
    _p("ldaps", "LDAPS", AUTH, ("tls",), transport="tcp", port=636),
    _p("radius", "RADIUS", AUTH, ("udp",), transport="udp", port=1812),
    _p("tacacs", "TACACS+", AUTH, ("tcp",), transport="tcp", port=49),
    # LOGGING / MAIL
    _p("syslog", "Syslog", LOGGING, ("udp",), transport="udp", port=514),
    _p("smtp", "SMTP", LOGGING, ("tcp",), transport="tcp", port=25),
    _p("imap", "IMAP", LOGGING, ("tcp",), transport="tcp", port=143),
    _p("pop3", "POP3", LOGGING, ("tcp",), transport="tcp", port=110),
    # ROUTING
    _p("bgp", "BGP", ROUTING, ("tcp",), transport="tcp", port=179,
       description="Inter-domain routing over TCP/179."),
    _p("ospf", "OSPF", ROUTING, ("ipv4",), transport="ip"),
    _p("rip", "RIP", ROUTING, ("udp",), transport="udp", port=520),
    # TUNNELING / VPN
    _p("gre", "GRE", VPN, ("ipv4",)),
    _p("ipsec", "IPsec", VPN, ("ipv4",), optional_dependencies=("udp",)),
    _p("ssl_vpn", "SSL VPN", VPN, ("tls",), port=443),
    _p("site_to_site_vpn", "Site-to-Site VPN", VPN, ("ipsec",), optional_dependencies=("gre",)),
    _p("ztna", "ZTNA", VPN, ("tls",), port=443),
    # SWITCHING (L2 - no ports)
    _p("vlan", "VLAN", SWITCHING),
    _p("stp", "STP", SWITCHING, ("vlan",)),
    _p("rstp", "RSTP", SWITCHING, ("stp",)),
    _p("mstp", "MSTP", SWITCHING, ("rstp",)),
    _p("lacp", "LACP", SWITCHING),
    _p("lldp", "LLDP", SWITCHING, description="Vendor-neutral neighbour discovery."),
    _p("cdp", "CDP", SWITCHING, description="Cisco neighbour discovery."),
    # WIRELESS
    _p("wifi", "WiFi", WIRELESS, ("vlan",)),
    _p("capwap", "CAPWAP", WIRELESS, ("udp",), transport="udp", port=5246,
       description="AP-to-controller control channel."),
    _p("802_1x", "802.1X", WIRELESS, ("radius",), description="Port-based access control."),
)

PROTOCOLS: dict[str, Protocol] = {p.identifier: p for p in _PROTOCOLS}


def get_protocol(identifier: str) -> Protocol | None:
    return PROTOCOLS.get(str(identifier).strip().lower())


def all_protocols() -> tuple[Protocol, ...]:
    return _PROTOCOLS


def protocols_in_category(category: str) -> tuple[Protocol, ...]:
    return tuple(p for p in _PROTOCOLS if p.category == category)

# SOP-016: Internet down, devices connected (layered triage)

The procedure itself (steps, commands per platform, expected results and what
each failure means) lives in the SOP Engine as **SOP-016**, seeded by
`backend/scripts/seed_sops.py`. This note explains how it fits the platform.
Edit the SOP there, not here, so there is one source.

## When it applies

Users are connected to the LAN or Wi-Fi but nothing on the internet works.
SOP-016 is the general first response. Two existing SOPs cover specific
causes and are linked from its steps:

- **SOP-006**: site internet down because the FortiGate WAN or Starlink IP changed.
- **SOP-009**: internet outage because the ISP router lost its static IP.

## Flow

```
1 Scope ── one user ─────────────► 4 (that device)
        ── one site ─────────────► 2
        ── all sites ────────────► shared dependency, escalate, 5 from two sites
2 Physical link / LEDs ── no link ─► cable, SFP, ONU/modem power
3 Router + switch interfaces ── down/down ─► 2 or 7 · errors ─► cable/duplex · saturated ─► congestion
4 IP + default gateway ── 169.254.x.x ─► DHCP · no WAN IP ─► ISP / SOP-009 · IP changed ─► SOP-006
5 Ping gateway → public IP → traceroute
        ── gateway fails ────────► LAN (2-4)
        ── public IP fails ──────► edge or ISP (7)
        ── public IP works ──────► 6
6 DNS (configured vs public resolver) ── only public works ─► internal DNS/forwarders
7 ISP access (GPON ONU/ONT/OLT): PON/LOS, registration, Rx/Tx optical levels
8 Escalate to the ISP with evidence (ticket number on the incident)
9 Verify stable recovery (15 min green) and record the root cause
```

## How HSDEEP recommends it

The SOP matcher (`app/services/sop/sop_matcher.py`) requires the incident's
category to equal the SOP's. SOP-016 is `connectivity` and leaves vendor,
platform and device type open, so it is a broad match for any connectivity
incident. The alert classifier (`app/services/alerting/event_classifier.py`)
now treats "internet" as a connectivity keyword, so an alert titled
"Internet down" is categorised `connectivity` (it used to fall through to
`availability` on the word "down", which kept SOP-006, SOP-009 and SOP-016
from being recommended for it).

## Safety

Every step is read-only. A router, firewall or ONU/ONT restart needs a change
record; record the WAN IP, routes and optical levels first. OLT checks are for
the ISP or staff with ISP access only.

"""PNETLab topology collector (Phase 9.7B).

PNETLab is the authoritative source for its own lab topology: it records exactly
which interface is patched to which. This collector reads that over PNETLab's
native JSON API and translates it into the EXISTING evidence contracts
(``NormalizedDevice`` / ``NormalizedInterface`` / ``NormalizedNeighbor``), so
everything downstream is reused unchanged:

    PNETLab API -> PNETLabCollector.normalize() -> NormalizedBundle
      -> BundleSaver -> DiscoveryService._write_neighbors
      -> resource_relationships -> TopologyLiveService -> the Phase 9.7A UI

ENDPOINTS USED (taken from PNETLab's own ``api.php`` / ``includes/api_*.php``,
not guessed):

* ``GET  <base>``                          -> session + ``XSRF-TOKEN`` cookies
* ``POST /store/public/auth/login/login``  -> authenticate (username/password)
* ``GET  /api/auth``                       -> session/permission check
* ``POST /api/labs/session/factory/create``-> open the lab session (``.unl``)
* ``GET  /api/labs/<lab>.unl/topology``    -> nodes + links for a lab
* ``GET  /api/labs/session/topology``      -> nodes + links for the open session

Every response uses PNETLab's JSend envelope: ``{code, status, message, data}``
with ``status`` in ``success | fail | forbidden``; ``forbidden`` is a permission
or credential problem, which is NEVER reported as "unsupported".

LINK SHAPE - the part that matters. PNETLab reports one object per connection::

    {"type": "ethernet",
     "source": "node3", "source_type": "node", "source_label": "e0/0",
     "destination": "node1", "destination_type": "node",
     "destination_label": "port2", "network_id": 1}

so local device, local interface, remote device and remote interface are all
EXPLICIT. A PNETLab link is therefore OBSERVED evidence, never an inference from
IP address, subnet, hostname or reachability.

HONESTY RULES
-------------
* Vendor/model come only from what PNETLab explicitly states (node type/template
  or image token). Anything else stays ``None`` -> "Unknown vendor"; a hostname
  or IP is never used to guess a vendor.
* A link endpoint PNETLab does not describe resolves to nothing: no edge is
  written and no device is invented.
* Nothing is hard-coded: base URL, credentials, lab path, TLS verification and
  timeout all come from the monitoring source or Settings (env). Credentials are
  never logged, and never appear in a raised message.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import unquote

import httpx

from app.collectors.base import (
    BaseCollector,
    CollectorAuthenticationError,
    CollectorConfigError,
    CollectorRegistry,
    ConnectionCheck,
    NormalizedBundle,
    NormalizedDevice,
    NormalizedInterface,
    NormalizedNeighbor,
)

logger = logging.getLogger(__name__)

#: Evidence token recorded on every edge this provider writes.
EVIDENCE_TYPE = "pnetlab_link"

#: Node status tokens PNETLab uses in its topology payload -> HSDEEP device status.
_NODE_STATUS = {
    "started": "up",
    "running": "up",
    "on": "up",
    "booted": "up",
    "stopped": "down",
    "off": "down",
    "down": "down",
    "halted": "down",
}

#: PNETLab node type/template/image token -> HSDEEP device type. Deliberately
#: conservative: an unknown token yields "other" rather than a guess.
_NODE_TYPES = {
    "router": "router",
    "iol": "router",
    "ioul": "router",
    "dynamips": "router",
    "csr1000v": "router",
    "c7200": "router",
    "fortigate": "firewall",
    "fortinet": "firewall",
    "firewall": "firewall",
    "paloalto": "firewall",
    "switch": "switch",
    "vios": "switch",
    "viosl2": "switch",
    "linux": "server",
    "docker": "server",
    "ubuntu": "server",
    "host": "server",
    "vpcs": "other",
    "cloud": "other",
}

#: Explicit vendor tokens PNETLab states about a node -> vendor label. Only
#: tokens a lab actually uses, and only when PNETLab states them.
_NODE_VENDORS = {
    "fortinet": "Fortinet",
    "fortigate": "Fortinet",
    "cisco": "Cisco",
    "iol": "Cisco",
    "ioul": "Cisco",
    "vios": "Cisco",
    "viosl2": "Cisco",
    "dynamips": "Cisco",
    "linux": "Linux",
    "ubuntu": "Linux",
    "debian": "Linux",
    "docker": "Linux",
    "vpcs": "VPCS",
    "windows": "Microsoft",
    "paloalto": "Palo Alto Networks",
}


class PNETLabError(RuntimeError):
    """Base class for PNETLab provider failures."""


class PNETLabUnreachable(PNETLabError):
    """The PNETLab host could not be reached (DNS/connect/transport)."""


class PNETLabTimeout(PNETLabError):
    """PNETLab did not answer within the configured timeout."""


class PNETLabAuthError(PNETLabError):
    """PNETLab rejected the credentials, or denied the request."""


class PNETLabMalformed(PNETLabError):
    """PNETLab answered, but not with a usable JSend envelope."""


@dataclass(frozen=True)
class PNETLabLink:
    """One connection PNETLab explicitly reports."""

    network_id: str | None
    link_type: str
    local_node: str
    local_interface: str | None
    remote_node: str | None
    remote_interface: str | None
    remote_is_node: bool


@CollectorRegistry.register("pnetlab")
class PNETLabCollector(BaseCollector):
    display_name = "PNETLab (lab topology)"
    implemented = True

    def __init__(self, source) -> None:
        super().__init__(source)
        self._client: httpx.AsyncClient | None = None
        #: Endpoints attempted for this lab, in order, with why each was skipped.
        #: Reported in the raw payload so an operator can see exactly what was
        #: read instead of trusting a summary.
        self._probes: list[dict[str, Any]] = []

    # -- configuration (monitoring source first, Settings as the default) ----

    @staticmethod
    def _settings():
        from app.core.config import get_settings

        return get_settings()

    def _config(self) -> dict[str, Any]:
        return dict(getattr(self.source, "config", None) or {})

    def _base_url(self) -> str:
        explicit = str(self._config().get("base_url") or "").strip()
        if explicit:
            return explicit.rstrip("/")
        configured = str(getattr(self._settings(), "pnetlab_base_url", "") or "").strip()
        if configured:
            return configured.rstrip("/")
        host = (self.source.hostname or "").strip()
        if not host:
            raise CollectorConfigError(
                "PNETLab config missing required field: hostname (or config.base_url)"
            )
        scheme = str(self._config().get("scheme") or "").strip().lower()
        if scheme not in ("http", "https"):
            scheme = "https" if (self.source.port or 443) in (443, 8443) else "http"
        port = self.source.port or (443 if scheme == "https" else 80)
        return f"{scheme}://{host}:{port}"

    def _username(self) -> str:
        return (self.source.username or self._settings().pnetlab_username or "").strip()

    def _password(self) -> str:
        # Held only in memory for the duration of one run: never logged, never
        # put into an exception message, never returned by an API.
        return self.source.password or self._settings().pnetlab_password or ""

    def _verify_ssl(self) -> bool:
        if self.source.verify_ssl is not None:
            return bool(self.source.verify_ssl)
        return bool(self._settings().pnetlab_verify_ssl)

    def _timeout(self) -> float:
        raw = self._config().get("timeout")
        if raw is not None:
            try:
                return float(raw)
            except (TypeError, ValueError):
                pass
        return float(self._settings().pnetlab_timeout_seconds or 15.0)

    def _lab_path(self) -> str:
        return str(
            self._config().get("lab_path") or self._settings().pnetlab_lab_path or ""
        ).strip()

    # -- HTTP ---------------------------------------------------------------

    async def _login(self) -> httpx.AsyncClient:
        if self._client is not None:
            return self._client
        if not self._username() or not self._password():
            raise CollectorConfigError(
                "PNETLab config missing required field: username/password"
            )

        client = httpx.AsyncClient(
            base_url=self._base_url(),
            verify=self._verify_ssl(),
            timeout=self._timeout(),
            follow_redirects=True,
            headers={"Content-Type": "application/json;charset=UTF-8"},
        )
        try:
            # PNETLab issues the session + XSRF cookies on the first request.
            bootstrap = await client.get("/")
            xsrf = client.cookies.get("XSRF-TOKEN") or bootstrap.cookies.get("XSRF-TOKEN")
            if xsrf:
                client.headers["X-XSRF-TOKEN"] = unquote(str(xsrf))
            response = await client.post(
                "/store/public/auth/login/login",
                json={
                    "username": self._username(),
                    "password": self._password(),
                    "html": "0",
                    "captcha": "",
                },
            )
        except httpx.TimeoutException as exc:
            await client.aclose()
            raise PNETLabTimeout("PNETLab did not answer the session request in time") from exc
        except httpx.HTTPError as exc:
            await client.aclose()
            raise PNETLabUnreachable(
                f"PNETLab is not reachable at {self._base_url()}"
            ) from exc

        if response.status_code in (401, 403):
            await client.aclose()
            raise PNETLabAuthError(
                f"PNETLab rejected the credentials (HTTP {response.status_code})"
            )
        if response.status_code >= 400:
            await client.aclose()
            raise PNETLabAuthError(f"PNETLab login returned HTTP {response.status_code}")
        self._client = client
        return client

    async def _call(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """One authenticated PNETLab call, unwrapped from its JSend envelope."""
        client = await self._login()
        try:
            response = await client.request(method, path, json=payload)
        except httpx.TimeoutException as exc:
            raise PNETLabTimeout(f"PNETLab {method} {path} timed out") from exc
        except httpx.HTTPError as exc:
            raise PNETLabUnreachable(
                f"PNETLab {method} {path} failed with a transport error"
            ) from exc

        if response.status_code in (401, 403):
            raise PNETLabAuthError(
                f"PNETLab denied {method} {path} (HTTP {response.status_code})"
            )
        if response.status_code >= 400:
            raise PNETLabMalformed(
                f"PNETLab returned HTTP {response.status_code} for {method} {path}"
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise PNETLabMalformed(
                f"PNETLab returned a non-JSON body for {method} {path}"
            ) from exc
        if not isinstance(body, dict) or "status" not in body:
            raise PNETLabMalformed(
                f"PNETLab returned no JSend envelope for {method} {path}"
            )
        if str(body.get("status")).lower() != "success":
            # `forbidden` is a permission/credential problem and `fail` is a
            # refusal: neither means "this device has no such capability".
            message = str(body.get("message") or "no message supplied")[:200]
            raise PNETLabMalformed(
                f"PNETLab reported status={body.get('status')} for {method} {path}: {message}"
            )
        return body

    # -- collector contract -------------------------------------------------

    async def authenticate(self) -> ConnectionCheck:
        """Verify reachability, credentials and permission.

        The failure TYPES stay distinct (unreachable / timeout / rejected
        credentials / unusable response) so a capability is never called
        unsupported merely because a credential or a network was wrong.
        """
        self._base_url()  # raises CollectorConfigError when not configured
        try:
            envelope = await self._call("GET", "/api/auth")
        except CollectorConfigError:
            raise
        except PNETLabAuthError as exc:
            return ConnectionCheck(success=False, message=f"PNETLab authentication failed: {exc}")
        except PNETLabTimeout as exc:
            return ConnectionCheck(success=False, message=f"PNETLab timed out: {exc}")
        except PNETLabUnreachable as exc:
            return ConnectionCheck(success=False, message=f"PNETLab unreachable: {exc}")
        except PNETLabMalformed as exc:
            return ConnectionCheck(
                success=False, message=f"PNETLab returned an unusable response: {exc}"
            )
        except Exception as exc:  # noqa: BLE001 - an expected failure never raises
            return ConnectionCheck(
                success=False, message=f"PNETLab connection failed: {type(exc).__name__}"
            )

        data = envelope.get("data")
        who = ""
        if isinstance(data, dict):
            who = str(data.get("username") or data.get("email") or "").strip()[:80]
        return ConnectionCheck(
            success=True,
            message=f"PNETLab reachable; session established{f' as {who}' if who else ''}.",
        )

    async def collect(self) -> dict[str, Any]:
        """Read the lab(s) and their topology exactly as PNETLab reports them."""
        self._probes = []
        lab_path = self._lab_path()
        if lab_path:
            labs: list[dict[str, Any]] = [{"path": lab_path, "source": "configured"}]
        else:
            labs = self._parse_labs(await self._call("GET", "/api/labs/"))

        topologies: list[dict[str, Any]] = []
        for lab in labs:
            path = str(lab.get("path") or "").strip()
            if not path:
                continue
            topology = await self._lab_topology(path)
            if topology is not None:
                topologies.append({"lab": lab, "topology": topology})

        return {
            "labs": labs,
            "topologies": topologies,
            "collected_at": datetime.now(timezone.utc).isoformat(),
            "lab_path": lab_path,
            "probes": list(self._probes),
        }

    async def aclose(self) -> None:
        """Release the HTTP session (never raises)."""
        client, self._client = self._client, None
        if client is None:
            return
        try:
            await client.aclose()
        except Exception:  # noqa: BLE001 - cleanup must not fail a run
            logger.debug("PNETLab client close failed", exc_info=True)

    # -- reading the lab (documented endpoint shapes, in order) --------------

    @staticmethod
    def _parse_labs(envelope: dict[str, Any]) -> list[dict[str, Any]]:
        """Labs the account can see, from a JSend ``data`` payload."""
        data = envelope.get("data")
        entries: list[Any] = []
        if isinstance(data, list):
            entries = data
        elif isinstance(data, dict):
            for key in ("labs", "data", "list"):
                if isinstance(data.get(key), list):
                    entries = data[key]
                    break
            else:
                entries = [value for value in data.values() if isinstance(value, dict)]
        labs: list[dict[str, Any]] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            path = str(entry.get("path") or entry.get("file") or "").strip()
            if not path:
                continue
            labs.append(
                {
                    "path": path,
                    "name": str(entry.get("name") or "").strip() or None,
                    "id": entry.get("id"),
                    "status": entry.get("status"),
                    "source": "pnetlab_api",
                }
            )
        return labs

    async def _lab_topology(self, path: str) -> dict[str, Any] | None:
        """Read one lab's topology from the first endpoint that answers.

        Ordered read-only-first. ``factory/create`` opens a session on PNETLab,
        so it is only tried last and is recorded as such in the probes list -
        nothing here is silent.
        """
        lab_file = path if path.startswith("/") else f"/{path}"
        attempts: list[tuple[str, str, dict[str, Any] | None]] = [
            ("GET", f"/api/labs{lab_file}/topology", None),
            ("GET", "/api/labs/session/topology", None),
            ("POST", "/api/labs/session/factory/create", {"path": lab_file}),
            ("POST", "/api/labs/session/factory/join", {"path": lab_file}),
        ]
        for method, endpoint, payload in attempts:
            try:
                body = await self._call(method, endpoint, payload)
            except PNETLabAuthError:
                # A permission problem must surface, not be swallowed as "no
                # topology here".
                raise
            except PNETLabError as exc:
                self._probes.append(
                    {"endpoint": f"{method} {endpoint}", "result": "skipped", "reason": str(exc)}
                )
                continue
            data = body.get("data")
            if data:
                self._probes.append({"endpoint": f"{method} {endpoint}", "result": "used"})
                return {"endpoint": f"{method} {endpoint}", "data": data}
            self._probes.append(
                {"endpoint": f"{method} {endpoint}", "result": "skipped", "reason": "empty payload"}
            )
        return None

    # -- pure parsing helpers ----------------------------------------------

    @staticmethod
    def _node_id(reference: Any) -> str:
        """``"node3"`` -> ``"3"``: PNETLab prefixes node references with 'node'."""
        text = str(reference).strip()
        if text.lower().startswith("node"):
            return text[4:] or text
        return text

    @staticmethod
    def _label(value: Any) -> str | None:
        text = str(value).strip() if value is not None else ""
        return text or None

    @staticmethod
    def _tokens(node: dict[str, Any]) -> list[str]:
        """Every explicit type/template/image token PNETLab states about a node."""
        raw = " ".join(
            str(node.get(key) or "")
            for key in ("type", "ntype", "node_type", "template", "image", "icon")
        ).lower()
        cleaned = raw.replace("/", " ").replace("-", " ").replace("_", " ").replace(".", " ")
        return [token for token in cleaned.split() if token]

    @classmethod
    def _device_type(cls, node: dict[str, Any]) -> str:
        for token in cls._tokens(node):
            mapped = _NODE_TYPES.get(token)
            if mapped:
                return mapped
        return "other"

    @classmethod
    def _vendor(cls, node: dict[str, Any]) -> tuple[str | None, str | None]:
        """(vendor, model) from what PNETLab STATES. Never derived from a name."""
        explicit = node.get("vendor")
        model = node.get("model")
        if explicit:
            return str(explicit).strip() or None, cls._label(model)
        for token in cls._tokens(node):
            mapped = _NODE_VENDORS.get(token)
            if mapped:
                return mapped, cls._label(model)
        return None, cls._label(model)

    @staticmethod
    def _status(node: dict[str, Any]) -> str:
        token = str(node.get("status") or node.get("state") or "").strip().lower()
        return _NODE_STATUS.get(token, "unknown")

    @staticmethod
    def _ip(node: dict[str, Any]) -> str | None:
        """A dotted quad PNETLab explicitly reported, or None. Never invented."""
        import re

        for key in ("ip_address", "management_ip", "mgmt_ip", "ip"):
            value = node.get(key)
            if value is None:
                continue
            text = str(value).strip()
            quad = re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", text)
            if quad and all(int(part) <= 255 for part in text.split(".")):
                return text
        return None

    # -- normalize (pure: PNETLab payload -> HSDEEP evidence contracts) ------

    @staticmethod
    def _lab_key(path: str) -> str:
        """A stable lab key derived from the lab PATH - never a hard-coded id."""
        text = str(path or "").strip().lstrip("/")
        if text.lower().endswith(".unl"):
            text = text[:-4]
        return text or "lab"

    @classmethod
    def _device_external_id(cls, lab_key: str, node_id: str) -> str:
        return f"pnetlab:{lab_key}:node{node_id}"

    @classmethod
    def _index_nodes(cls, candidate: Any) -> dict[str, dict]:
        """Node records keyed by PNETLab node id (dict-keyed and list forms)."""
        found: dict[str, dict] = {}
        if isinstance(candidate, dict):
            for key, value in candidate.items():
                if isinstance(value, dict):
                    node = dict(value)
                    node.setdefault("id", key)
                    found[str(node["id"])] = node
        elif isinstance(candidate, list):
            for value in candidate:
                if isinstance(value, dict) and value.get("id") is not None:
                    found[str(value["id"])] = value
        return found

    @classmethod
    def _split_payload(cls, data: Any) -> tuple[dict[str, dict], list[dict]]:
        """(nodes, link rows) from either documented topology payload shape.

        PNETLab's reference topology implementation emits a LIST of link objects
        (``$output['data'][] = $link``); node data may arrive beside it as a dict
        keyed by node id. Anything unrecognised is ignored - never guessed into a
        device.
        """
        nodes: dict[str, dict] = {}
        links: list[dict] = []
        if isinstance(data, dict):
            for key in ("nodes", "node_list", "nodelist"):
                nodes.update(cls._index_nodes(data.get(key)))
            for key in ("links", "topology", "connections"):
                candidate = data.get(key)
                if isinstance(candidate, list):
                    links.extend(item for item in candidate if isinstance(item, dict))
        elif isinstance(data, list):
            for item in data:
                if not isinstance(item, dict):
                    continue
                if "source" in item and "destination" in item:
                    links.append(item)
                else:
                    nodes.update(cls._index_nodes([item]))
        return nodes, links

    @classmethod
    def _link(cls, raw: dict[str, Any]) -> PNETLabLink | None:
        """One reported connection, or None when the endpoints are not stated."""
        source = str(raw.get("source") or "").strip()
        destination = str(raw.get("destination") or "").strip()
        if not source or not destination:
            return None
        return PNETLabLink(
            network_id=None if raw.get("network_id") is None else str(raw.get("network_id")),
            link_type=str(raw.get("type") or "ethernet").strip() or "ethernet",
            local_node=source,
            local_interface=cls._label(raw.get("source_label") or raw.get("source_interface")),
            remote_node=destination,
            remote_interface=cls._label(
                raw.get("destination_label") or raw.get("destination_interface")
            ),
            remote_is_node=str(raw.get("destination_type") or "node").strip().lower() == "node"
            and str(raw.get("source_type") or "node").strip().lower() == "node",
        )

    @classmethod
    def _interfaces_of_node(cls, node: dict[str, Any]) -> list[tuple[str, str, str, str]]:
        """(external_id, name, admin_status, oper_status) PNETLab explicitly lists."""
        rows: list[tuple[str, str, str, str]] = []
        for key in ("ethernets", "interfaces", "eth"):
            candidate = node.get(key)
            if isinstance(candidate, list):
                entries = candidate
            elif isinstance(candidate, dict):
                entries = [
                    dict(value, id=index) if isinstance(value, dict) else value
                    for index, value in candidate.items()
                ]
            else:
                continue
            for entry in entries:
                if isinstance(entry, dict):
                    name = cls._label(entry.get("name") or entry.get("label"))
                    if not name:
                        continue
                    external_id = (
                        str(entry.get("id")) if entry.get("id") is not None else name
                    )
                    token = str(entry.get("status") or entry.get("state") or "").lower()
                    mapped = _NODE_STATUS.get(token, "unknown")
                    rows.append((external_id, name, mapped, mapped))
                else:
                    name = cls._label(entry)
                    if name:
                        rows.append((name, name, "unknown", "unknown"))
        return rows

    def normalize(self, raw: dict[str, Any]) -> NormalizedBundle:
        """PNETLab nodes/interfaces/links -> NormalizedBundle (no inference)."""
        devices: list[NormalizedDevice] = []
        interfaces: list[NormalizedInterface] = []
        neighbors: list[NormalizedNeighbor] = []
        device_ids: set[str] = set()
        interface_keys: set[tuple[str, str]] = set()
        topologies = raw.get("topologies") or []
        link_rows_total = 0
        network_links = 0
        dangling_links = 0

        def add_device(
            lab_key: str, lab_path: str, node_id: str, node: dict[str, Any] | None,
            name_source: str,
        ) -> str:
            external_id = self._device_external_id(lab_key, node_id)
            if external_id in device_ids:
                return external_id
            node = node or {}
            vendor, model = self._vendor(node)
            devices.append(
                NormalizedDevice(
                    external_id=external_id,
                    # PNETLab's own node name, or its own node id when a link
                    # referenced a node the payload did not describe in detail.
                    name=self._label(node.get("name")) or f"node{node_id}",
                    device_type=self._device_type(node),
                    vendor=vendor,
                    model=model,
                    os_version=self._label(node.get("image")),
                    hostname=self._label(node.get("name")),
                    ip_address=self._ip(node),
                    status=self._status(node),
                    serial_number=self._label(node.get("serial") or node.get("serial_number")),
                    # The existing source-metadata field carries the LAB identity,
                    # so which lab (and which node) this came from is never lost -
                    # without inventing a site hierarchy for it.
                    custom_metadata={
                        "pnetlab_lab_path": lab_path,
                        "pnetlab_lab_key": lab_key,
                        "pnetlab_node_id": node_id,
                        "pnetlab_node_name_source": name_source,
                        "pnetlab_node_type": str(node.get("type") or node.get("ntype") or "") or None,
                        "pnetlab_template": str(node.get("template") or "") or None,
                    },
                )
            )
            device_ids.add(external_id)
            return external_id

        def add_interface(
            device_external_id: str, external_id: str, name: str,
            admin_status: str, oper_status: str,
        ) -> None:
            key = (device_external_id, str(external_id))
            if not device_external_id or key in interface_keys:
                return
            interface_keys.add(key)
            interfaces.append(
                NormalizedInterface(
                    device_external_id=device_external_id,
                    external_id=str(external_id),
                    name=name,
                    admin_status=admin_status,
                    oper_status=oper_status,
                )
            )

        for entry in topologies:
            lab = entry.get("lab") or {}
            topology = entry.get("topology") or {}
            lab_path = str(lab.get("path") or raw.get("lab_path") or "")
            lab_key = self._lab_key(lab_path)
            nodes, link_rows = self._split_payload(topology.get("data"))
            link_rows_total += len(link_rows)

            for node_id, node in sorted(nodes.items(), key=lambda item: str(item[0])):
                external_id = add_device(lab_key, lab_path, node_id, node, "node_payload")
                for iface_id, name, admin, oper in self._interfaces_of_node(node):
                    add_interface(external_id, iface_id, name, admin, oper)

            for row in link_rows:
                link = self._link(row)
                if link is None:
                    # A row with no stated endpoints is unusable: it is counted
                    # and skipped, never guessed into a connection.
                    dangling_links += 1
                    continue
                local_id = add_device(
                    lab_key, lab_path, self._node_id(link.local_node), None, "link_reference"
                )
                if link.local_interface:
                    add_interface(
                        local_id, link.local_interface, link.local_interface,
                        "unknown", "unknown",
                    )
                if not link.remote_is_node:
                    # The far end is a PNETLab network/cloud object, not a device:
                    # a real object, but not a device adjacency, so no edge is
                    # invented for it. Counted and reported instead.
                    network_links += 1
                    continue
                remote_node_id = self._node_id(link.remote_node or "")
                if not remote_node_id:
                    dangling_links += 1
                    continue
                remote_id = add_device(
                    lab_key, lab_path, remote_node_id, None, "link_reference"
                )
                if link.remote_interface:
                    add_interface(
                        remote_id, link.remote_interface, link.remote_interface,
                        "unknown", "unknown",
                    )
                # PNETLab states ONE connection; both ends really see it, so both
                # directions are recorded - each as source-observed evidence whose
                # peer is named by PNETLab's OWN node reference (an identity, not
                # a hostname, so resolution never becomes a name guess).
                neighbors.append(
                    NormalizedNeighbor(
                        device_external_id=local_id,
                        local_interface=link.local_interface,
                        remote_external_id=remote_id,
                        remote_interface=link.remote_interface,
                        evidence_type=EVIDENCE_TYPE,
                    )
                )
                neighbors.append(
                    NormalizedNeighbor(
                        device_external_id=remote_id,
                        local_interface=link.remote_interface,
                        remote_external_id=local_id,
                        remote_interface=link.local_interface,
                        evidence_type=EVIDENCE_TYPE,
                    )
                )

        if not topologies:
            link_status = "unavailable"
        elif link_rows_total == 0:
            link_status = "empty"
        else:
            link_status = "collected"

        logger.info(
            "PNETLab normalize(): labs=%s devices=%s interfaces=%s link_rows=%s "
            "node_links=%s to_network=%s dangling=%s",
            len(topologies), len(devices), len(interfaces), link_rows_total,
            len(neighbors) // 2, network_links, dangling_links,
        )

        return NormalizedBundle(
            devices=devices,
            interfaces=interfaces,
            neighbors=neighbors,
            neighbor_status={EVIDENCE_TYPE: link_status},
        )

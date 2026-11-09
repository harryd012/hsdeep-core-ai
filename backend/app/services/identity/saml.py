"""
SAML 2.0 single sign-on, service-provider side (enterprise security programme S6).

SP-initiated Web Browser SSO: an AuthnRequest goes to the IdP with the
HTTP-Redirect binding; the IdP answers our assertion consumer service (ACS)
with the HTTP-POST binding.

Verification, in this order, and only from signed data:
  1. The XML is parsed with entities, DTDs and network access disabled.
  2. Encrypted assertions are refused (not supported), and the response must
     contain exactly one Assertion. Signature-wrapping attacks hide a forged
     assertion next to a signed one; with one assertion there is nothing to
     confuse.
  3. The XML signature is verified with signxml against the IdP's
     certificate(s). Everything afterwards is read from the element signxml
     reports as signed (the Response or the Assertion), never from the
     unverified document, and that signed assertion must be the one assertion
     counted in step 2.
  4. On the signed assertion: Issuer is the IdP's entity ID; the Conditions
     window (2 minutes of clock skew); the audience is our entity ID; the
     bearer SubjectConfirmationData names our ACS URL, is unexpired and answers
     our own request (InResponseTo). Unsolicited (IdP-initiated) responses are
     refused, and each request can be answered once (the login state is
     consumed), so a captured response cannot be replayed.
"""
from __future__ import annotations

import base64
import secrets
import zlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from cryptography import x509
from lxml import etree
from signxml import XMLVerifier
from signxml.exceptions import InvalidInput, InvalidSignature

from app.core.config import get_settings

NS = {
    "samlp": "urn:oasis:names:tc:SAML:2.0:protocol",
    "saml": "urn:oasis:names:tc:SAML:2.0:assertion",
    "md": "urn:oasis:names:tc:SAML:2.0:metadata",
    "ds": "http://www.w3.org/2000/09/xmldsig#",
}
_A = "{%s}" % NS["saml"]
_P = "{%s}" % NS["samlp"]
SKEW = timedelta(minutes=2)
SUCCESS = "urn:oasis:names:tc:SAML:2.0:status:Success"
BEARER = "urn:oasis:names:tc:SAML:2.0:cm:bearer"
EMAIL_ATTRIBUTES = (
    "email", "mail", "emailaddress", "Email",
    "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress",
    "urn:oid:0.9.2342.19200300.100.1.3",
)
NAME_ATTRIBUTES = ("displayName", "name", "http://schemas.microsoft.com/identity/claims/displayname",
                   "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name")


class SamlError(ValueError):
    """A user-facing reason the SAML sign-in could not complete."""


def _parser() -> etree.XMLParser:
    return etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False, remove_comments=True)


def parse_xml(data: bytes) -> etree._Element:
    try:
        root = etree.fromstring(data, parser=_parser())
    except etree.XMLSyntaxError as exc:
        raise SamlError("The identity provider sent malformed XML") from exc
    if root.getroottree().docinfo.doctype:
        raise SamlError("XML with a DOCTYPE is not accepted")
    return root


# ---- Service-provider identity ------------------------------------------------

def _api() -> str:
    return get_settings().api_public_url.rstrip("/")


def sp_entity_id(tenant_id) -> str:
    return f"{_api()}/api/auth/sso/saml/{tenant_id}"


def acs_url(tenant_id) -> str:
    return f"{_api()}/api/auth/sso/saml/{tenant_id}/acs"


def sp_metadata(tenant_id) -> str:
    entity, acs = sp_entity_id(tenant_id), acs_url(tenant_id)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<md:EntityDescriptor xmlns:md="{NS["md"]}" entityID="{entity}">'
        '<md:SPSSODescriptor AuthnRequestsSigned="false" WantAssertionsSigned="true" '
        'protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol">'
        '<md:NameIDFormat>urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress</md:NameIDFormat>'
        f'<md:AssertionConsumerService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST" Location="{acs}" index="0" isDefault="true"/>'
        "</md:SPSSODescriptor></md:EntityDescriptor>"
    )


# ---- IdP configuration --------------------------------------------------------

def normalize_certificates(text: str) -> str:
    """One or more certificates (PEM, or bare base64 as pasted from metadata) -> PEM text."""
    blocks = []
    pem_parts = [p for p in text.split("-----END CERTIFICATE-----") if "BEGIN CERTIFICATE" in p]
    raw = [p.split("-----BEGIN CERTIFICATE-----", 1)[1] for p in pem_parts] or [text]
    for body in raw:
        b64 = "".join(body.split())
        if not b64:
            continue
        pem = "-----BEGIN CERTIFICATE-----\n" + "\n".join(b64[i:i + 64] for i in range(0, len(b64), 64)) + "\n-----END CERTIFICATE-----\n"
        try:
            x509.load_pem_x509_certificate(pem.encode())
        except ValueError as exc:
            raise SamlError("That is not a valid X.509 certificate") from exc
        blocks.append(pem)
    if not blocks:
        raise SamlError("Paste the identity provider's signing certificate")
    return "".join(blocks)


def split_certificates(pem_text: str) -> list[str]:
    return [p + "-----END CERTIFICATE-----\n" for p in pem_text.split("-----END CERTIFICATE-----") if "BEGIN CERTIFICATE" in p]


@dataclass
class IdpMetadata:
    entity_id: str
    sso_url: str
    certificates: str


def parse_idp_metadata(xml_text: str) -> IdpMetadata:
    root = parse_xml(xml_text.encode("utf-8"))
    ed = root if root.tag == "{%s}EntityDescriptor" % NS["md"] else root.find(".//md:EntityDescriptor", NS)
    if ed is None or not ed.get("entityID"):
        raise SamlError("No EntityDescriptor with an entityID in that metadata")
    idp = ed.find("md:IDPSSODescriptor", NS)
    if idp is None:
        raise SamlError("The metadata describes no identity provider (IDPSSODescriptor)")
    sso = next((s.get("Location") for s in idp.findall("md:SingleSignOnService", NS)
                if s.get("Binding", "").endswith("HTTP-Redirect")), None)
    if not sso:
        raise SamlError("The identity provider offers no HTTP-Redirect sign-in endpoint")
    certs = [c.text for k in idp.findall("md:KeyDescriptor", NS) if k.get("use") in (None, "signing")
             for c in k.findall(".//ds:X509Certificate", NS) if c.text]
    if not certs:
        raise SamlError("The metadata contains no signing certificate")
    return IdpMetadata(entity_id=ed.get("entityID"), sso_url=sso, certificates=normalize_certificates("\n".join(
        f"-----BEGIN CERTIFICATE-----\n{c.strip()}\n-----END CERTIFICATE-----" for c in certs)))


# ---- AuthnRequest -------------------------------------------------------------

def new_request_id() -> str:
    return "_" + secrets.token_hex(20)


def authn_request_url(*, tenant_id, idp_sso_url: str, request_id: str, relay_state: str, now: datetime | None = None) -> str:
    instant = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    xml = (
        f'<samlp:AuthnRequest xmlns:samlp="{NS["samlp"]}" xmlns:saml="{NS["saml"]}" ID="{request_id}" Version="2.0" '
        f'IssueInstant="{instant}" Destination="{idp_sso_url}" AssertionConsumerServiceURL="{acs_url(tenant_id)}" '
        'ProtocolBinding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST">'
        f"<saml:Issuer>{sp_entity_id(tenant_id)}</saml:Issuer>"
        '<samlp:NameIDPolicy Format="urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress" AllowCreate="true"/>'
        "</samlp:AuthnRequest>"
    )
    deflater = zlib.compressobj(9, zlib.DEFLATED, -15)
    encoded = base64.b64encode(deflater.compress(xml.encode()) + deflater.flush()).decode()
    sep = "&" if "?" in idp_sso_url else "?"
    return f"{idp_sso_url}{sep}{urlencode({'SAMLRequest': encoded, 'RelayState': relay_state})}"


# ---- Response verification ----------------------------------------------------

@dataclass
class SamlIdentity:
    subject: str
    email: str | None
    name: str | None


def _time(value: str | None) -> datetime | None:
    if not value:
        return None
    v = value.strip().replace("Z", "+00:00")
    if "." in v:  # trim fractional seconds beyond microseconds
        head, _, tail = v.partition(".")
        digits = "".join(ch for ch in tail if ch.isdigit())
        zone = tail[len(digits):]
        v = f"{head}.{digits[:6]}{zone}"
    try:
        dt = datetime.fromisoformat(v)
    except ValueError as exc:
        raise SamlError("The assertion has an unreadable timestamp") from exc
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def verify_response(
    saml_response_b64: str,
    *,
    certificates: str,
    idp_entity_id: str,
    sp_entity: str,
    acs: str,
    request_id: str,
    email_attribute: str | None = None,
    now: datetime | None = None,
) -> SamlIdentity:
    now = now or datetime.now(timezone.utc)
    try:
        raw = base64.b64decode("".join(saml_response_b64.split()), validate=True)
    except (ValueError, TypeError) as exc:
        raise SamlError("The identity provider's response is not valid base64") from exc

    # 1-2: parse safely; one plain assertion only.
    doc = parse_xml(raw)
    if doc.tag != f"{_P}Response":
        raise SamlError("That is not a SAML response")
    if doc.findall(f".//{_A}EncryptedAssertion"):
        raise SamlError("Encrypted assertions are not supported; turn off assertion encryption for this application")
    assertions = doc.findall(f".//{_A}Assertion")
    if len(assertions) != 1:
        raise SamlError("The response must contain exactly one assertion")
    status = doc.find(f"{_P}Status/{_P}StatusCode")
    if status is None or status.get("Value") != SUCCESS:
        raise SamlError("The identity provider did not sign you in")

    # 3: verify the signature; read only from what it covers.
    signed = None
    for cert in split_certificates(certificates):
        try:
            signed = XMLVerifier().verify(raw, x509_cert=cert).signed_xml
            break
        except (InvalidSignature, InvalidInput):
            continue
    if signed is None:
        raise SamlError("The response is not signed by your identity provider's certificate")
    if signed.tag == f"{_A}Assertion":
        assertion = signed
    elif signed.tag == f"{_P}Response":
        inner = signed.findall(f".//{_A}Assertion")
        if len(inner) != 1:
            raise SamlError("The response must contain exactly one assertion")
        assertion = inner[0]
    else:
        raise SamlError("The signature covers neither the response nor the assertion")
    if assertion.get("ID") != assertions[0].get("ID"):
        raise SamlError("The signed assertion is not the one in the response")

    # 4: conditions on the signed assertion.
    if (assertion.findtext(f"{_A}Issuer") or "").strip() != idp_entity_id:
        raise SamlError("The assertion was issued by a different identity provider")
    cond = assertion.find(f"{_A}Conditions")
    if cond is None:
        raise SamlError("The assertion has no validity conditions")
    nb, noa = _time(cond.get("NotBefore")), _time(cond.get("NotOnOrAfter"))
    if (nb and now + SKEW < nb) or (noa and now - SKEW >= noa):
        raise SamlError("The assertion has expired or is not valid yet")
    audiences = [a.text.strip() for a in cond.findall(f"{_A}AudienceRestriction/{_A}Audience") if a.text]
    if sp_entity not in audiences:
        raise SamlError("The assertion is meant for a different application (audience)")

    subject = assertion.find(f"{_A}Subject")
    name_id = subject.find(f"{_A}NameID") if subject is not None else None
    confirmed = False
    for sc in (subject.findall(f"{_A}SubjectConfirmation") if subject is not None else []):
        data = sc.find(f"{_A}SubjectConfirmationData")
        if sc.get("Method") != BEARER or data is None:
            continue
        exp = _time(data.get("NotOnOrAfter"))
        if data.get("Recipient") == acs and data.get("InResponseTo") == request_id and exp and now - SKEW < exp:
            confirmed = True
            break
    if not confirmed:
        raise SamlError("The assertion does not answer this sign-in (recipient, request or expiry)")
    if name_id is None or not (name_id.text or "").strip():
        raise SamlError("The assertion has no subject")

    attrs: dict[str, str] = {}
    for attr in assertion.findall(f"{_A}AttributeStatement/{_A}Attribute"):
        value = attr.findtext(f"{_A}AttributeValue")
        if value:
            attrs[attr.get("Name", "")] = value.strip()
    subject_value = name_id.text.strip()
    email = None
    candidates = [email_attribute] if email_attribute else []
    candidates += list(EMAIL_ATTRIBUTES)
    for key in candidates:
        if key and "@" in attrs.get(key, ""):
            email = attrs[key].lower()
            break
    if email is None and "@" in subject_value:
        email = subject_value.lower()
    name = next((attrs[k] for k in NAME_ATTRIBUTES if attrs.get(k)), None)
    return SamlIdentity(subject=subject_value, email=email, name=name)

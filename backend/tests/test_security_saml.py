"""SAML 2.0 single sign-on (enterprise security programme S6).

A fake identity provider with a real RSA key and self-signed certificate signs
responses with signxml. Covered: metadata import, SP metadata, the
SP-initiated flow end to end, and refusals for a forged signature, another
IdP's certificate, an expired assertion, a wrong audience, a wrong
recipient / InResponseTo, a replayed response, an unsolicited response, an
encrypted assertion and two signature-wrapping attacks.
"""
from __future__ import annotations

import base64
import copy
import uuid
import zlib
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import pytest_asyncio
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from lxml import etree
from signxml import XMLSigner
from sqlalchemy import select

from app.api.routes import auth as auth_routes
from app.core.auth import create_access_token
from app.core.db import AsyncSessionLocal
from app.main import app
from app.models.identity import SsoLoginState
from app.models.user import User
from app.services.identity import domains as dom
from app.services.identity import saml

IDP_ENTITY = "https://idp.saml.test/metadata"
IDP_SSO = "https://idp.saml.test/sso"
NS_P = "urn:oasis:names:tc:SAML:2.0:protocol"
NS_A = "urn:oasis:names:tc:SAML:2.0:assertion"


def _keypair(cn: str):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=365)).sign(key, hashes.SHA256()))
    key_pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    return key_pem, cert.public_bytes(serialization.Encoding.PEM)


IDP_KEY, IDP_CERT = _keypair("Fake IdP")
EVIL_KEY, EVIL_CERT = _keypair("Attacker")


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def build_response(*, tenant_id, request_id: str, email: str, sign: str = "assertion", key=IDP_KEY, cert=IDP_CERT,
                   audience: str | None = None, recipient: str | None = None, issuer: str = IDP_ENTITY,
                   not_on_or_after: datetime | None = None, extra_assertion: bool = False, encrypted: bool = False) -> bytes:
    now = datetime.now(timezone.utc)
    noa = not_on_or_after or now + timedelta(minutes=5)
    aid = "_a" + uuid.uuid4().hex
    assertion = f"""<saml:Assertion xmlns:saml="{NS_A}" ID="{aid}" Version="2.0" IssueInstant="{_iso(now)}">
<saml:Issuer>{issuer}</saml:Issuer>
<saml:Subject><saml:NameID Format="urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress">{email}</saml:NameID>
<saml:SubjectConfirmation Method="urn:oasis:names:tc:SAML:2.0:cm:bearer">
<saml:SubjectConfirmationData InResponseTo="{request_id}" NotOnOrAfter="{_iso(noa)}" Recipient="{recipient or saml.acs_url(tenant_id)}"/>
</saml:SubjectConfirmation></saml:Subject>
<saml:Conditions NotBefore="{_iso(now - timedelta(minutes=1))}" NotOnOrAfter="{_iso(noa)}">
<saml:AudienceRestriction><saml:Audience>{audience or saml.sp_entity_id(tenant_id)}</saml:Audience></saml:AudienceRestriction>
</saml:Conditions>
<saml:AttributeStatement><saml:Attribute Name="displayName"><saml:AttributeValue>Sam Lee</saml:AttributeValue></saml:Attribute></saml:AttributeStatement>
</saml:Assertion>"""
    a_el = etree.fromstring(assertion.encode())
    if sign == "assertion":
        a_el = XMLSigner(c14n_algorithm="http://www.w3.org/2001/10/xml-exc-c14n#").sign(a_el, key=key, cert=cert)
    resp = etree.fromstring(f"""<samlp:Response xmlns:samlp="{NS_P}" xmlns:saml="{NS_A}" ID="_r{uuid.uuid4().hex}" Version="2.0"
 IssueInstant="{_iso(now)}" InResponseTo="{request_id}"><saml:Issuer>{issuer}</saml:Issuer>
<samlp:Status><samlp:StatusCode Value="urn:oasis:names:tc:SAML:2.0:status:Success"/></samlp:Status></samlp:Response>""".encode())
    if encrypted:
        etree.SubElement(resp, f"{{{NS_A}}}EncryptedAssertion")
    else:
        resp.append(a_el)
    if extra_assertion:
        evil = copy.deepcopy(etree.fromstring(assertion.replace(email, "attacker@evil.test").replace(aid, "_evil").encode()))
        resp.append(evil)
    if sign == "response":
        resp = XMLSigner(c14n_algorithm="http://www.w3.org/2001/10/xml-exc-c14n#").sign(resp, key=key, cert=cert)
    return etree.tostring(resp)


def b64(xml: bytes) -> str:
    return base64.b64encode(xml).decode()


# ---------------------------------------------------------------------------
# Pure verification
# ---------------------------------------------------------------------------

TENANT = uuid.uuid4()
REQ = "_req123"


def _verify(xml: bytes, **kw):
    return saml.verify_response(b64(xml), certificates=IDP_CERT.decode(), idp_entity_id=IDP_ENTITY,
                                sp_entity=saml.sp_entity_id(TENANT), acs=saml.acs_url(TENANT), request_id=REQ, **kw)


def test_valid_assertion_or_response_signature():
    ident = _verify(build_response(tenant_id=TENANT, request_id=REQ, email="Sam@Corp.Test"))
    assert (ident.email, ident.subject, ident.name) == ("sam@corp.test", "Sam@Corp.Test", "Sam Lee")
    assert _verify(build_response(tenant_id=TENANT, request_id=REQ, email="sam@corp.test", sign="response")).email == "sam@corp.test"


@pytest.mark.parametrize("kwargs,message", [
    ({"sign": "none"}, "not signed"),
    ({"key": EVIL_KEY, "cert": EVIL_CERT}, "not signed"),
    ({"issuer": "https://other-idp.test"}, "different identity provider"),
    ({"audience": "https://someone-else.test"}, "different application"),
    ({"recipient": "https://evil.test/acs"}, "does not answer"),
    ({"not_on_or_after": datetime.now(timezone.utc) - timedelta(minutes=10)}, "expired"),
    ({"encrypted": True}, "Encrypted assertions"),
    ({"extra_assertion": True}, "exactly one assertion"),
])
def test_refusals(kwargs, message):
    with pytest.raises(saml.SamlError, match=message):
        _verify(build_response(tenant_id=TENANT, request_id=REQ, email="sam@corp.test", **kwargs))


def test_wrong_request_id_is_refused():
    with pytest.raises(saml.SamlError, match="does not answer"):
        _verify(build_response(tenant_id=TENANT, request_id="_someone_elses", email="sam@corp.test"))


def test_wrapping_signed_assertion_swapped_for_unsigned_one():
    """Move the signed assertion's content around and put a forged, unsigned one
    in its place: the signature no longer covers the assertion that is read."""
    signed = etree.fromstring(build_response(tenant_id=TENANT, request_id=REQ, email="sam@corp.test"))
    a = signed.find(f"{{{NS_A}}}Assertion")
    forged = copy.deepcopy(a)
    forged.find(f"{{{NS_A}}}Subject/{{{NS_A}}}NameID").text = "attacker@evil.test"
    for sig in forged.findall("{http://www.w3.org/2000/09/xmldsig#}Signature"):
        forged.remove(sig)
    forged.set("ID", "_forged")
    signed.replace(a, forged)
    status = signed.find(f"{{{NS_P}}}Status")
    ext = etree.SubElement(status, f"{{{NS_P}}}StatusDetail")
    ext.append(a)  # the genuine signed assertion hidden elsewhere
    with pytest.raises(saml.SamlError):
        _verify(etree.tostring(signed))


def test_doctype_and_garbage_are_refused():
    with pytest.raises(saml.SamlError):
        _verify(b'<!DOCTYPE x [<!ENTITY e "boom">]><samlp:Response xmlns:samlp="' + NS_P.encode() + b'">&e;</samlp:Response>')
    with pytest.raises(saml.SamlError):
        saml.verify_response("not base64!!", certificates=IDP_CERT.decode(), idp_entity_id=IDP_ENTITY,
                             sp_entity="x", acs="y", request_id=REQ)


def test_metadata_parsing_and_certificates():
    raw = "".join(IDP_CERT.decode().split("\n")[1:-2])
    meta = f"""<md:EntityDescriptor xmlns:md="urn:oasis:names:tc:SAML:2.0:metadata" xmlns:ds="http://www.w3.org/2000/09/xmldsig#" entityID="{IDP_ENTITY}">
<md:IDPSSODescriptor protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol">
<md:KeyDescriptor use="signing"><ds:KeyInfo><ds:X509Data><ds:X509Certificate>{raw}</ds:X509Certificate></ds:X509Data></ds:KeyInfo></md:KeyDescriptor>
<md:SingleSignOnService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST" Location="{IDP_SSO}/post"/>
<md:SingleSignOnService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect" Location="{IDP_SSO}"/>
</md:IDPSSODescriptor></md:EntityDescriptor>"""
    parsed = saml.parse_idp_metadata(meta)
    assert (parsed.entity_id, parsed.sso_url) == (IDP_ENTITY, IDP_SSO)
    assert saml.split_certificates(parsed.certificates)
    with pytest.raises(saml.SamlError):
        saml.normalize_certificates("not a certificate")
    assert "AssertionConsumerService" in saml.sp_metadata(TENANT)


# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def saml_org(tenant, seeded_subscription, monkeypatch):
    monkeypatch.setattr(auth_routes._auth_limiter, "limit", 10**9)
    domain = f"saml-{uuid.uuid4().hex[:6]}.test"
    records: dict[str, list[str]] = {}

    async def lookup(name):
        return records.get(name, [])

    monkeypatch.setattr(dom, "lookup_txt", lookup)
    token = create_access_token(user_id=uuid.UUID(int=0), tenant_id=tenant.id, email="admin@hsdeep.local",
                                is_superuser=False, permissions=frozenset({"security.manage"}))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                                 headers={"Authorization": f"Bearer {token}"}) as c:
        d = (await c.post("/api/settings/identity/domains", json={"domain": domain})).json()
        records[d["txt_name"]] = [d["txt_value"]]
        assert (await c.post(f"/api/settings/identity/domains/{d['id']}/verify")).status_code == 200
        assert (await c.put("/api/settings/identity/sso", json={"protocol": "saml", "enabled": True})).status_code == 422
        bad = await c.put("/api/settings/identity/saml", json={"idp_sso_url": "http://insecure.test/sso"})
        assert bad.status_code == 422
        r = await c.put("/api/settings/identity/saml", json={"idp_entity_id": IDP_ENTITY, "idp_sso_url": IDP_SSO,
                                                             "idp_certificates": IDP_CERT.decode()})
        assert r.status_code == 200 and r.json()["saml"]["certificates"][0]["subject"] == "CN=Fake IdP"
        assert (await c.put("/api/settings/identity/sso", json={"protocol": "saml", "enabled": True})).status_code == 200
    async with AsyncSessionLocal() as db:
        db.add(User(tenant_id=tenant.id, email=f"sam@{domain}", password_hash=None))
        await db.commit()
    return tenant, domain


async def _start(domain):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as anon:
        r = await anon.post("/api/auth/sso/start", json={"email": f"sam@{domain}", "next": "/incidents"})
    assert r.status_code == 200, r.text
    url = urlparse(r.json()["redirect_url"])
    q = parse_qs(url.query)
    assert f"{url.scheme}://{url.netloc}{url.path}" == IDP_SSO
    xml = zlib.decompress(base64.b64decode(q["SAMLRequest"][0]), -15)
    request_id = etree.fromstring(xml).get("ID")
    return q["RelayState"][0], request_id


async def _post_acs(tenant_id, response: bytes, relay: str | None):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as anon:
        data = {"SAMLResponse": b64(response)}
        if relay:
            data["RelayState"] = relay
        r = await anon.post(f"/api/auth/sso/saml/{tenant_id}/acs", data=data)
    assert r.status_code == 303
    return {k: v[0] for k, v in parse_qs(urlparse(r.headers["location"]).query).items()}


async def test_saml_end_to_end_with_replay_and_unsolicited(saml_org):
    tenant, domain = saml_org
    relay, request_id = await _start(domain)
    async with AsyncSessionLocal() as db:
        assert (await db.get(SsoLoginState, relay)).nonce == request_id
    response = build_response(tenant_id=tenant.id, request_id=request_id, email=f"sam@{domain}")
    params = await _post_acs(tenant.id, response, relay)
    assert params["next"] == "/incidents" and "code" in params
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as anon:
        tokens = await anon.post("/api/auth/sso/exchange", json={"code": params["code"]})
        assert tokens.status_code == 200
        me = await anon.get("/api/auth/me", headers={"Authorization": f"Bearer {tokens.json()['access_token']}"})
        assert me.json()["email"] == f"sam@{domain}"

    # The same response again (replay), or with no RelayState (unsolicited), is refused.
    assert "not started here" in (await _post_acs(tenant.id, response, relay))["error"]
    assert "not started here" in (await _post_acs(tenant.id, response, None))["error"]

    # A valid signature for an email on an unverified domain is refused.
    relay2, request2 = await _start(domain)
    other = build_response(tenant_id=tenant.id, request_id=request2, email="sam@not-verified.test")
    assert "not verified" in (await _post_acs(tenant.id, other, relay2))["error"]

    # The SP metadata is public.
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as anon:
        md = await anon.get(f"/api/auth/sso/saml/{tenant.id}/metadata")
        assert md.status_code == 200 and saml.acs_url(tenant.id) in md.text
    async with AsyncSessionLocal() as db:
        user = (await db.execute(select(User).where(User.email == f"sam@{domain}"))).scalar_one()
        assert user.tenant_id == tenant.id

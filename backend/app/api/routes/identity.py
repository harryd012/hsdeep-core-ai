"""
Enterprise identity API (enterprise security programme S6).

Administration (security.manage):
  GET    /api/settings/identity                       domains, SSO configuration, SCIM status
  POST   /api/settings/identity/domains               add a domain (returns its DNS TXT record)
  POST   /api/settings/identity/domains/{id}/verify   check the TXT record
  DELETE /api/settings/identity/domains/{id}
  PUT    /api/settings/identity/sso                   protocol, OIDC provider, enable, require SSO, JIT
  PUT    /api/settings/identity/saml                  SAML identity provider (or its metadata XML)
  POST   /api/settings/identity/scim-token            new SCIM token (shown once; replaces the old one)
  DELETE /api/settings/identity/scim-token            revoke SCIM access

Sign-in (public, rate limited):
  POST   /api/auth/sso/start                          email -> the identity provider's URL
  GET    /api/auth/sso/oidc/callback                  the OIDC provider returns here -> browser
  GET    /api/auth/sso/saml/{tenant_id}/metadata      our SAML service-provider metadata
  POST   /api/auth/sso/saml/{tenant_id}/acs           the SAML provider posts here -> browser
  POST   /api/auth/sso/exchange                       one-time handoff code -> session tokens
"""
from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timezone
from urllib.parse import urlencode

from typing import Literal

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.auth import _check_auth_rate, _client, get_auth_service
from app.core.auth import Principal, require_permission
from app.core.config import get_settings
from app.core.db import get_db
from app.core.entitlements import require_active_subscription
from app.core.security import SecretCrypto
from app.models.identity import SsoLoginState, TenantDomain, TenantSamlConfig, TenantSsoConfig
from app.schemas.auth import TokenResponse
from app.services.audit_service import AuditService
from app.services.auth_service import AuthService
from app.services.identity import domains as dom
from app.services.identity import oidc
from app.services.identity import saml

router = APIRouter(tags=["identity"])
_guard = Depends(require_active_subscription)


# ---- Administration ----------------------------------------------------------

class DomainOut(BaseModel):
    id: uuid.UUID
    domain: str
    verified: bool
    verified_at: datetime | None
    txt_name: str
    txt_value: str


class SsoOut(BaseModel):
    protocol: str = "oidc"
    issuer: str | None = None
    client_id: str | None = None
    client_secret_set: bool = False
    enabled: bool = False
    require_sso: bool = False
    jit_provisioning: bool = False
    default_role_id: uuid.UUID | None = None
    redirect_uri: str
    scim_enabled: bool = False
    scim_token_prefix: str | None = None
    scim_base_url: str


class SamlOut(BaseModel):
    idp_entity_id: str | None = None
    idp_sso_url: str | None = None
    #: Subject and expiry of each stored IdP signing certificate.
    certificates: list[dict] = []
    email_attribute: str | None = None
    sp_entity_id: str
    acs_url: str
    metadata_url: str


class IdentityOut(BaseModel):
    domains: list[DomainOut]
    sso: SsoOut
    saml: SamlOut


class SamlUpdate(BaseModel):
    """Either paste the IdP's metadata XML, or set the fields one by one."""

    metadata_xml: str | None = Field(None, max_length=200_000)
    idp_entity_id: str | None = Field(None, max_length=500)
    idp_sso_url: str | None = Field(None, max_length=1000)
    idp_certificates: str | None = Field(None, max_length=50_000)
    email_attribute: str | None = Field(None, max_length=255)


class DomainCreate(BaseModel):
    domain: str = Field(..., min_length=3, max_length=253)


class SsoUpdate(BaseModel):
    protocol: Literal["oidc", "saml"] | None = None
    issuer: str | None = Field(None, max_length=500)
    client_id: str | None = Field(None, max_length=255)
    #: Write-only. Omit to keep the stored secret; "" removes it (public clients).
    client_secret: str | None = Field(None, max_length=2000)
    enabled: bool | None = None
    require_sso: bool | None = None
    jit_provisioning: bool | None = None
    default_role_id: uuid.UUID | None = None


def _domain_out(d: TenantDomain) -> DomainOut:
    return DomainOut(id=d.id, domain=d.domain, verified=d.verified_at is not None, verified_at=d.verified_at,
                     txt_name=f"{dom.CHALLENGE_PREFIX}.{d.domain}", txt_value=f"{dom.TOKEN_PREFIX}{d.verification_token}")


async def _identity(db: AsyncSession, tenant_id: uuid.UUID) -> IdentityOut:
    rows = (await db.execute(select(TenantDomain).where(TenantDomain.tenant_id == tenant_id).order_by(TenantDomain.created_at))).scalars().all()
    cfg = await db.get(TenantSsoConfig, tenant_id)
    base = get_settings().api_public_url.rstrip("/")
    sso = SsoOut(redirect_uri=oidc.redirect_uri(), scim_base_url=f"{base}/api/scim/v2")
    if cfg is not None:
        sso = SsoOut(
            issuer=cfg.issuer, client_id=cfg.client_id, client_secret_set=bool(cfg.client_secret_encrypted),
            enabled=cfg.enabled, require_sso=cfg.require_sso, jit_provisioning=cfg.jit_provisioning,
            default_role_id=cfg.default_role_id, redirect_uri=oidc.redirect_uri(),
            scim_enabled=bool(cfg.scim_token_hash), scim_token_prefix=cfg.scim_token_prefix, scim_base_url=f"{base}/api/scim/v2",
        )
    return IdentityOut(domains=[_domain_out(d) for d in rows], sso=sso, saml=await _saml_out(db, tenant_id))


def _cert_summary(pem_text: str | None) -> list[dict]:
    from cryptography import x509

    out = []
    for pem in saml.split_certificates(pem_text or ""):
        try:
            cert = x509.load_pem_x509_certificate(pem.encode())
        except ValueError:
            continue
        out.append({"subject": cert.subject.rfc4514_string(), "expires": cert.not_valid_after_utc.isoformat()})
    return out


async def _saml_out(db: AsyncSession, tenant_id: uuid.UUID) -> SamlOut:
    row = await db.get(TenantSamlConfig, tenant_id)
    base = get_settings().api_public_url.rstrip("/")
    return SamlOut(
        idp_entity_id=row.idp_entity_id if row else None, idp_sso_url=row.idp_sso_url if row else None,
        certificates=_cert_summary(row.idp_certificates if row else None),
        email_attribute=row.email_attribute if row else None,
        sp_entity_id=saml.sp_entity_id(tenant_id), acs_url=saml.acs_url(tenant_id),
        metadata_url=f"{base}/api/auth/sso/saml/{tenant_id}/metadata",
    )


async def _saml_ready(db: AsyncSession, tenant_id: uuid.UUID) -> bool:
    row = await db.get(TenantSamlConfig, tenant_id)
    return bool(row and row.idp_entity_id and row.idp_sso_url and row.idp_certificates)


async def _config(db: AsyncSession, tenant_id: uuid.UUID) -> TenantSsoConfig:
    cfg = await db.get(TenantSsoConfig, tenant_id)
    if cfg is None:
        cfg = TenantSsoConfig(tenant_id=tenant_id, protocol="oidc", enabled=False, require_sso=False, jit_provisioning=False)
        db.add(cfg)
        await db.flush()
    return cfg


async def _audit(db, principal: Principal, action: str, description: str, old=None, new=None) -> None:
    await AuditService(db).record_entity(
        tenant_id=principal.tenant_id, action=action, entity_type="identity", entity_id=principal.tenant_id,
        actor_id=principal.user_id, actor_email=principal.email, description=description, old_values=old, new_values=new,
    )


@router.get("/settings/identity", response_model=IdentityOut)
async def get_identity(principal: Principal = Depends(require_permission("security.manage")), db: AsyncSession = Depends(get_db)):
    return await _identity(db, principal.tenant_id)


@router.post("/settings/identity/domains", response_model=DomainOut, status_code=status.HTTP_201_CREATED, dependencies=[_guard])
async def add_domain(payload: DomainCreate, principal: Principal = Depends(require_permission("security.manage")),
                     db: AsyncSession = Depends(get_db)):
    try:
        row = await dom.add_domain(db, principal.tenant_id, payload.domain)
    except dom.DomainError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    await _audit(db, principal, "identity.domain_added", f"Domain {row.domain} added (unverified)", new={"domain": row.domain})
    await db.commit()
    return _domain_out(row)


@router.post("/settings/identity/domains/{domain_id}/verify", response_model=DomainOut, dependencies=[_guard])
async def verify_domain(domain_id: uuid.UUID, principal: Principal = Depends(require_permission("security.manage")),
                        db: AsyncSession = Depends(get_db)):
    row = (await db.execute(select(TenantDomain).where(TenantDomain.id == domain_id, TenantDomain.tenant_id == principal.tenant_id))).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Domain not found")
    try:
        ok = await dom.verify_domain(db, row)
    except dom.DomainError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if not ok:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=f"The TXT record was not found at {dom.CHALLENGE_PREFIX}.{row.domain}. DNS changes can take a few minutes.")
    await _audit(db, principal, "identity.domain_verified", f"Domain {row.domain} verified", new={"domain": row.domain})
    await db.commit()
    return _domain_out(row)


@router.delete("/settings/identity/domains/{domain_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[_guard])
async def remove_domain(domain_id: uuid.UUID, principal: Principal = Depends(require_permission("security.manage")),
                        db: AsyncSession = Depends(get_db)):
    row = (await db.execute(select(TenantDomain).where(TenantDomain.id == domain_id, TenantDomain.tenant_id == principal.tenant_id))).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Domain not found")
    await db.delete(row)
    await _audit(db, principal, "identity.domain_removed", f"Domain {row.domain} removed", old={"domain": row.domain})
    await db.commit()


@router.put("/settings/identity/sso", response_model=IdentityOut, dependencies=[_guard])
async def update_sso(payload: SsoUpdate, principal: Principal = Depends(require_permission("security.manage")),
                     db: AsyncSession = Depends(get_db)):
    cfg = await _config(db, principal.tenant_id)
    changes = payload.model_dump(exclude_unset=True)
    old, new = {}, {}
    secret = changes.pop("client_secret", None)
    if secret is not None:
        cfg.client_secret_encrypted = SecretCrypto().encrypt(secret) if secret else None
        new["client_secret"] = "changed" if secret else "removed"
    for field, value in changes.items():
        if field == "issuer" and value:
            value = value.strip().rstrip("/")
        if getattr(cfg, field) != value:
            old[field], new[field] = getattr(cfg, field), value
            setattr(cfg, field, value)
    if cfg.enabled:
        if not await dom.verified_domains(db, principal.tenant_id):
            raise HTTPException(status_code=422, detail="Verify at least one email domain before turning SSO on.")
        if cfg.protocol == "saml":
            if not await _saml_ready(db, principal.tenant_id):
                raise HTTPException(status_code=422, detail="Set the SAML identity provider (entity ID, sign-in URL, certificate) before turning SSO on.")
        else:
            if not (cfg.issuer and cfg.client_id):
                raise HTTPException(status_code=422, detail="Set the issuer and client ID before turning SSO on.")
            try:
                await oidc.discover(cfg.issuer)
            except (oidc.SsoError, Exception) as exc:  # noqa: BLE001 - any discovery failure is a config error
                raise HTTPException(status_code=422, detail=f"Could not read the identity provider's configuration at {cfg.issuer}: {exc}") from exc
    if cfg.require_sso and not cfg.enabled:
        raise HTTPException(status_code=422, detail="Turn SSO on before requiring it.")
    if new:
        await _audit(db, principal, "identity.sso_updated", f"Single sign-on changed: {', '.join(sorted(new))}",
                     old={k: str(v) if isinstance(v, uuid.UUID) else v for k, v in old.items()},
                     new={k: str(v) if isinstance(v, uuid.UUID) else v for k, v in new.items()})
    await db.commit()
    return await _identity(db, principal.tenant_id)


@router.put("/settings/identity/saml", response_model=IdentityOut, dependencies=[_guard])
async def update_saml(payload: SamlUpdate, principal: Principal = Depends(require_permission("security.manage")),
                      db: AsyncSession = Depends(get_db)):
    """Set the SAML identity provider. Metadata XML (from Entra ID, Okta, ADFS, Google …) fills
    the entity ID, sign-in URL and signing certificates in one go."""
    row = await db.get(TenantSamlConfig, principal.tenant_id)
    if row is None:
        row = TenantSamlConfig(tenant_id=principal.tenant_id)
        db.add(row)
    try:
        if payload.metadata_xml:
            meta = saml.parse_idp_metadata(payload.metadata_xml)
            row.idp_entity_id, row.idp_sso_url, row.idp_certificates = meta.entity_id, meta.sso_url, meta.certificates
        if payload.idp_entity_id is not None:
            row.idp_entity_id = payload.idp_entity_id.strip() or None
        if payload.idp_sso_url is not None:
            url = payload.idp_sso_url.strip()
            if url and not url.startswith("https://"):
                raise saml.SamlError("The sign-in URL must use https://")
            row.idp_sso_url = url or None
        if payload.idp_certificates is not None:
            row.idp_certificates = saml.normalize_certificates(payload.idp_certificates) if payload.idp_certificates.strip() else None
    except saml.SamlError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if payload.email_attribute is not None:
        row.email_attribute = payload.email_attribute.strip() or None
    await _audit(db, principal, "identity.saml_updated", "SAML identity provider changed",
                 new={"idp_entity_id": row.idp_entity_id, "idp_sso_url": row.idp_sso_url,
                      "certificates": len(saml.split_certificates(row.idp_certificates or ""))})
    await db.commit()
    return await _identity(db, principal.tenant_id)


@router.post("/settings/identity/scim-token", dependencies=[_guard])
async def rotate_scim_token(principal: Principal = Depends(require_permission("security.manage")),
                            db: AsyncSession = Depends(get_db)) -> dict[str, str]:
    """A new SCIM bearer token for your identity provider. Shown once; the previous token stops working."""
    cfg = await _config(db, principal.tenant_id)
    token = "scim_" + secrets.token_urlsafe(32)
    cfg.scim_token_hash = hashlib.sha256(token.encode()).hexdigest()
    cfg.scim_token_prefix = token[:12]
    await _audit(db, principal, "identity.scim_token_created", "SCIM provisioning token created", new={"prefix": cfg.scim_token_prefix})
    await db.commit()
    return {"token": token, "prefix": cfg.scim_token_prefix}


@router.delete("/settings/identity/scim-token", status_code=status.HTTP_204_NO_CONTENT, dependencies=[_guard])
async def revoke_scim_token(principal: Principal = Depends(require_permission("security.manage")), db: AsyncSession = Depends(get_db)):
    cfg = await _config(db, principal.tenant_id)
    cfg.scim_token_hash = None
    cfg.scim_token_prefix = None
    await _audit(db, principal, "identity.scim_token_revoked", "SCIM provisioning token revoked")
    await db.commit()


# ---- Sign-in ------------------------------------------------------------------

class SsoStart(BaseModel):
    email: str = Field(..., min_length=3, max_length=320)
    next: str | None = Field(None, max_length=500)


def _safe_next(path: str | None) -> str | None:
    return path if path and path.startswith("/") and not path.startswith("//") else None


@router.post("/auth/sso/start")
async def sso_start(payload: SsoStart, db: AsyncSession = Depends(get_db), _: None = Depends(_check_auth_rate)) -> dict[str, str]:
    """Find the organization by the email's verified domain and return its identity provider's sign-in URL."""
    domain = dom.email_domain(payload.email)
    tenant_id = await dom.tenant_for_domain(db, domain) if domain else None
    cfg = await db.get(TenantSsoConfig, tenant_id) if tenant_id else None
    if cfg is None or not cfg.enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Single sign-on isn't set up for this email domain.")
    if cfg.protocol == "saml":
        row = await db.get(TenantSamlConfig, cfg.tenant_id)
        if row is None or not row.idp_sso_url:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Single sign-on isn't set up for this email domain.")
        relay, request_id = secrets.token_urlsafe(32), saml.new_request_id()
        db.add(SsoLoginState(state=relay, tenant_id=cfg.tenant_id, nonce=request_id, code_verifier=secrets.token_urlsafe(16),
                             next_path=_safe_next(payload.next), expires_at=datetime.now(timezone.utc) + oidc.STATE_TTL))
        await db.commit()
        return {"redirect_url": saml.authn_request_url(tenant_id=cfg.tenant_id, idp_sso_url=row.idp_sso_url,
                                                       request_id=request_id, relay_state=relay)}
    try:
        url = await oidc.start_login(db, cfg, login_hint=payload.email.strip(), next_path=_safe_next(payload.next))
    except oidc.SsoError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    await db.commit()
    return {"redirect_url": url}


def _to_app(**params: str) -> RedirectResponse:
    base = get_settings().app_public_url.rstrip("/")
    return RedirectResponse(f"{base}/sso/complete?{urlencode(params)}", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/auth/sso/oidc/callback", include_in_schema=False)
async def sso_callback(
    code: str | None = Query(None, max_length=4000),
    state: str | None = Query(None, max_length=200),
    error: str | None = Query(None, max_length=200),
    db: AsyncSession = Depends(get_db),
):
    if error or not code or not state:
        return _to_app(error="The identity provider did not sign you in.")
    try:
        result = await oidc.complete_login(db, state=state, code=code)
    except oidc.SsoError as exc:
        await db.commit()  # the one-time state stays consumed
        return _to_app(error=str(exc))
    handoff = await oidc.create_handoff(db, result.user)
    await AuditService(db).record(
        tenant_id=result.user.tenant_id, action="auth.sso_login", actor_id=result.user.id, actor_email=result.user.email,
        entity_type="auth", description=f"{result.user.email} signed in with SSO" + (" (account created)" if result.created else ""),
    )
    await db.commit()
    params = {"code": handoff}
    if result.next_path:
        params["next"] = result.next_path
    return _to_app(**params)


class SsoExchange(BaseModel):
    code: str = Field(..., min_length=10, max_length=200)


@router.post("/auth/sso/exchange", response_model=TokenResponse)
async def sso_exchange(payload: SsoExchange, request: Request, db: AsyncSession = Depends(get_db),
                       service: AuthService = Depends(get_auth_service), _: None = Depends(_check_auth_rate)):
    """Trade the one-time handoff code (60 seconds) for a session. The identity
    provider authenticated the user, so no platform MFA step is added."""
    handed = await oidc.redeem_handoff(db, payload.code)
    # Reload through the repository so roles (permissions) are loaded.
    user = await service.users.get_by_id(handed.id, handed.tenant_id) if handed is not None else None
    if user is None:
        await db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="This sign-in has expired. Start again.")
    ip_address, user_agent = _client(request)
    await service.users.touch_last_login(user)
    access, refresh, expires_in = await service._issue_tokens(user, ip_address=ip_address, user_agent=user_agent)
    await db.commit()
    return TokenResponse(access_token=access, refresh_token=refresh, expires_in=expires_in)


# ---- SAML 2.0 -----------------------------------------------------------------

@router.get("/auth/sso/saml/{tenant_id}/metadata", include_in_schema=False)
async def saml_sp_metadata(tenant_id: uuid.UUID):
    """Our service-provider metadata, to upload to the identity provider."""
    return Response(saml.sp_metadata(tenant_id), media_type="application/samlmetadata+xml")


@router.post("/auth/sso/saml/{tenant_id}/acs", include_in_schema=False)
async def saml_acs(
    tenant_id: uuid.UUID,
    SAMLResponse: str = Form(..., max_length=500_000),  # noqa: N803 - SAML binding field name
    RelayState: str | None = Form(None, max_length=200),  # noqa: N803 - SAML binding field name
    db: AsyncSession = Depends(get_db),
):
    """Assertion consumer service (HTTP-POST binding). Only answers to our own
    requests are accepted: RelayState names a one-time login state."""
    state = await db.get(SsoLoginState, RelayState) if RelayState else None
    if state is None or state.tenant_id != tenant_id:
        return _to_app(error="This sign-in was not started here, or has expired. Start again from the sign-in page.")
    await db.delete(state)  # one use, whatever happens next
    await db.flush()
    cfg = await db.get(TenantSsoConfig, tenant_id)
    row = await db.get(TenantSamlConfig, tenant_id)
    try:
        if state.expires_at <= datetime.now(timezone.utc):
            raise saml.SamlError("This sign-in has expired. Start again.")
        if cfg is None or not cfg.enabled or cfg.protocol != "saml" or row is None or not row.idp_certificates:
            raise saml.SamlError("SAML single sign-on is not enabled for this organization.")
        identity = saml.verify_response(
            SAMLResponse, certificates=row.idp_certificates, idp_entity_id=row.idp_entity_id or "",
            sp_entity=saml.sp_entity_id(tenant_id), acs=saml.acs_url(tenant_id), request_id=state.nonce,
            email_attribute=row.email_attribute,
        )
        if not identity.email:
            raise saml.SamlError("The identity provider did not share your email address.")
        if dom.email_domain(identity.email) not in await dom.verified_domains(db, tenant_id):
            raise saml.SamlError("Your email domain is not verified for this organization.")
        user, created = await oidc.resolve_user(db, cfg, email=identity.email, subject=identity.subject,
                                                name=identity.name, provider="saml")
        if not user.is_active:
            raise saml.SamlError("Your account is disabled. Contact your administrator.")
    except (saml.SamlError, oidc.SsoError) as exc:
        await db.commit()  # the one-time state stays consumed
        return _to_app(error=str(exc))
    handoff = await oidc.create_handoff(db, user)
    await AuditService(db).record(
        tenant_id=tenant_id, action="auth.sso_login", actor_id=user.id, actor_email=user.email, entity_type="auth",
        description=f"{user.email} signed in with SAML SSO" + (" (account created)" if created else ""),
    )
    await db.commit()
    params = {"code": handoff}
    if state.next_path:
        params["next"] = state.next_path
    return _to_app(**params)

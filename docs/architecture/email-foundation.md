# Email foundation

Status: approved 2026-10-30 and implemented (batch 9, see
docs/PLATFORM_LEDGER.md). Differences from the proposal, decided during the
build:

- Transport `none` added: production refuses `log`, and `render.yaml` ships
  `EMAIL_TRANSPORT=none` so the pilot deploys before SMTP is configured; the
  email flows then answer 503 instead of silently dropping links.
- SMTP uses the standard library (`smtplib` in a worker thread) rather than
  `aiosmtplib`, so no new dependency.
- Verification is a separate flag, `users.requires_email_verification`, set
  only for self-service sign-ups while email is on, so operator-created and
  existing users are never locked out. Sign-up still returns a session (API
  contract unchanged, `email_verification_sent` added); the next sign-in
  needs the confirmed email (403 with a clear message).

## Problem

The platform sends no email. There is no password reset, no email
verification, no user invitation, and the per-tenant notification settings
(`notification_settings.email_enabled`, `email_recipients`, plus Slack and
PagerDuty fields) are stored but never delivered by anything. Public sign-up
stays off (`PUBLIC_SIGNUP_ENABLED=false`) until this exists.

## Scope

1. **Outbound mail service** `app/services/email/`:
   - `EmailSender` protocol with two transports: SMTP (`aiosmtplib`, works with
     any provider: SES SMTP, Postmark, SendGrid, Mailgun, SMTP2GO) and a
     `LogTransport` for development that logs the message metadata (never the
     token) instead of sending.
   - Settings: `EMAIL_TRANSPORT` (`smtp` | `log`), `SMTP_HOST`, `SMTP_PORT`,
     `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_STARTTLS`, `EMAIL_FROM`,
     `APP_PUBLIC_URL` (link base). Production startup refuses `log`.
   - Delivery runs on the worker through a small `email_outbox` table
     (queued -> sent | failed, attempts, last_error), so an SMTP outage never
     fails a request and retries are bounded and visible.
2. **One-time tokens** `account_tokens` table: purpose (`password_reset` |
   `email_verify` | `invite`), user, SHA-256 hash of the token (plaintext only
   in the email), expires_at, used_at. Single use; reset 30 min, verify 24 h,
   invite 7 days.
3. **Flows** (all rate limited per IP and per email; responses never reveal
   whether an email exists):
   - `POST /api/auth/password/forgot` -> email with reset link.
   - `POST /api/auth/password/reset` (token + new password) -> sets password,
     revokes refresh tokens, clears lockout.
   - `POST /api/auth/email/verify` (token); `users.email_verified_at`.
   - `POST /api/auth/users` (existing, admin) gains `send_invite: true`;
     `POST /api/auth/invite/accept` sets the first password.
   - Sign-up (when enabled) creates the user unverified and sends the verify
     email; login of an unverified self-signup user is refused.
4. **Frontend**: `/forgot-password`, `/reset-password`, `/verify-email`,
   `/accept-invite` pages on the existing auth layout; "Forgot password?" link
   on `/login`.
5. **Audit**: every token issue/use writes an `AuditEvent`
   (`auth.password_reset_requested`, `auth.password_reset`, `auth.invite_sent`,
   `auth.email_verified`).

## Out of scope (next steps)

- Alert/incident notification delivery (email/Slack/PagerDuty) reusing the
  same outbox; separate design once the transport exists.
- MFA enforcement and SSO.

## Data and migration

New tables `email_outbox`, `account_tokens`; new column
`users.email_verified_at` (existing users backfilled as verified so nobody is
locked out). One additive migration with a clean downgrade.

## Security

Tokens are 32 random bytes, stored hashed, single use, short expiry; links use
`APP_PUBLIC_URL` only (never the request Host header). SMTP credentials come
from the environment (Render `sync: false`). No email content contains
secrets other than the one-time link.

## Tests

Token issue/consume/expiry/reuse, enumeration-safe responses, rate limits,
outbox retry and failure states, transport selection, production refusal of
the log transport, the four frontend pages' API contracts.

## Estimate

About 2 batches: backend (transport, outbox, tokens, flows, migration, tests),
then frontend pages and docs.

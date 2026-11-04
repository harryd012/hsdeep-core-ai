/**
 * /trust — Trust & security (enterprise security programme S5). Public.
 *
 * Every statement here describes a control the platform implements today
 * (docs/security/ENTERPRISE_SECURITY_PROGRAMME.md). No certification is claimed:
 * HSDEEP CORE AI holds no SOC 2, ISO 27001 or similar attestation yet.
 */
import type { Metadata } from "next";
import Link from "next/link";
import "@/app/styles/trust.css";

export const metadata: Metadata = {
  title: "Trust & security · HSDEEP CORE AI",
  description: "How HSDEEP CORE AI protects your organization's data.",
};

const SECTIONS: { title: string; items: string[] }[] = [
  {
    title: "Tenant isolation",
    items: [
      "Every record belongs to one organization; the organization comes from your signed-in identity, never from the request.",
      "Platform support can enter your organization only under a time-limited, audited break-glass grant that your own users can revoke.",
    ],
  },
  {
    title: "Identity and access",
    items: [
      "Passwords are hashed with Argon2id. Your administrators set the password rules, lockout threshold and idle-session timeout.",
      "Two-step verification with authenticator apps and one-time recovery codes; your organization can require it for everyone.",
      "Role-based access with custom roles, and a list of every signed-in device that each user (or an administrator) can sign out.",
      "Single sign-on (OIDC, SAML) and SCIM provisioning are on the roadmap, not available yet.",
    ],
  },
  {
    title: "Network and API",
    items: [
      "An optional IP allowlist limits sign-in, the app and API keys to your office and VPN networks.",
      "API keys carry only the permissions they are given, never more than the person who created them holds.",
      "Sign-in attempts are rate limited per address across every server.",
    ],
  },
  {
    title: "Encryption",
    items: [
      "Credentials for your monitoring sources and integrations, and two-step verification secrets, are encrypted at rest (Fernet: AES-128-CBC with HMAC-SHA256).",
      "Encryption keys can be rotated without downtime; every stored secret is re-encrypted with the new key.",
      "Traffic is protected with HTTPS; browsers are told to use HTTPS only (HSTS) when the deployment enforces it.",
    ],
  },
  {
    title: "Audit and integrity",
    items: [
      "Sign-ins, user and role changes, security and privacy settings, data access and exports are recorded in your organization's audit log, which administrators can filter and export.",
      "The audit log is tamper-evident: each event is chained to the previous one with SHA-256, so a changed or deleted record is detected, and you can keep a checkpoint outside the platform.",
    ],
  },
  {
    title: "Privacy and your data",
    items: [
      "You decide whether AI features may use your data. Credentials are always removed from AI prompts; email and IP addresses can be removed too.",
      "AI runs on a model server operated with the platform (by default on the same infrastructure), not on a third-party AI service.",
      "Credentials are removed from application logs.",
      "You set how long metric history and raw events are kept.",
      "You can export everything your organization owns at any time, and an owner can delete the organization: all of its data is removed after a 30-day grace period during which the request can be cancelled.",
    ],
  },
];

export default function TrustPage() {
  return (
    <main className="trust">
      <header className="trust__head">
        <Link href="/login" className="trust__brand">HSDEEP CORE <span>AI</span></Link>
        <h1>Trust &amp; security</h1>
        <p>
          How we protect your organization&apos;s data. Everything below describes controls that are in the product today; anything
          not yet available is marked as such.
        </p>
      </header>
      <div className="trust__grid">
        {SECTIONS.map((s) => (
          <section key={s.title} className="trust__card">
            <h2>{s.title}</h2>
            <ul>{s.items.map((i) => <li key={i}>{i}</li>)}</ul>
          </section>
        ))}
      </div>
      <section className="trust__card trust__note">
        <h2>Certifications</h2>
        <p>
          HSDEEP CORE AI does not hold a SOC 2, ISO 27001 or similar certification yet. We will publish reports here when we have them.
          For security questionnaires or to report a vulnerability, contact your HSDEEP account team.
        </p>
      </section>
      <footer className="trust__foot">© {new Date().getFullYear()} HSDEEP CORE AI</footer>
    </main>
  );
}

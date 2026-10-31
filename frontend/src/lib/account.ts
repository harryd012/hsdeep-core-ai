/**
 * Account self-service (public endpoints, no session needed):
 * password reset, email verification and invitation acceptance.
 */
import { apiFetch } from "@/lib/api";

interface Detail {
  detail: string;
}

function post(path: string, body: Record<string, string>): Promise<Detail> {
  return apiFetch<Detail>(path, { method: "POST", body: JSON.stringify(body) });
}

/** Always resolves the same way whether or not the email has an account. */
export function requestPasswordReset(email: string): Promise<Detail> {
  return post("/api/auth/password/forgot", { email });
}

export function resetPassword(token: string, password: string): Promise<Detail> {
  return post("/api/auth/password/reset", { token, password });
}

export function verifyEmail(token: string): Promise<Detail> {
  return post("/api/auth/email/verify", { token });
}

export function acceptInvite(token: string, password: string): Promise<Detail> {
  return post("/api/auth/invite/accept", { token, password });
}

/** Message for a failed account request, by status. */
export function accountErrorMessage(status: number, fallback: string): string {
  if (status === 0) return "Can't reach the server. Check your connection and try again.";
  if (status === 400) return "This link is invalid or has expired. Request a new one.";
  if (status === 429) return "Too many attempts. Wait a minute and try again.";
  if (status === 503) return "Email isn't set up on this deployment yet. Ask your administrator.";
  return fallback;
}

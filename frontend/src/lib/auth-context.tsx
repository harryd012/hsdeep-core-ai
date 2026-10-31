"use client";

/**
 * AuthProvider — real frontend session state (PRODUCTION_READINESS.md P0-1).
 *
 * Wraps the existing backend auth wiring in lib/api.ts (loginWithCredentials,
 * getMe, token storage) with a React context so the rest of the app can ask
 * "am I logged in / who am I" without every component reaching into
 * localStorage directly.
 *
 * This does NOT change backend auth behavior at all — it only gives the
 * frontend a real login/logout/session lifecycle instead of relying solely
 * on the dev-only auto-bootstrap login in lib/api.ts.
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  ApiError,
  clearAuthToken,
  getActiveTenantOverride,
  clearActiveTenantOverride,
  DEFAULT_TENANT_ID,
  getAuthToken,
  getMe,
  loginWithCredentials,
  logoutSession,
  signupTenant,
  type LoginCredentials,
  type MeResponse,
  type SignupRequest,
} from "@/lib/api";

interface AuthContextValue {
  /** The current authenticated user, or null when not logged in. */
  user: MeResponse | null;
  /** True while the initial session check (or a login/logout) is in flight. */
  isLoading: boolean;
  /** Convenience derived flag — true only once `user` has been resolved. */
  isAuthenticated: boolean;
  /** Set when the last session check failed for a reason OTHER than "not
   * logged in yet" (e.g. the backend was unreachable). Null otherwise. */
  sessionError: string | null;
  /** Perform a real login against POST /api/auth/login. Throws ApiError on
   * failure (bad credentials, network error, timeout) — callers (the login
   * form) are expected to catch this and show a message. */
  login: (creds: LoginCredentials) => Promise<void>;
  /** Create a brand-new tenant + Owner user + trial subscription via
   * POST /api/billing/signup, then load the resulting session. Throws
   * ApiError on failure (duplicate email, rate limit, network error). */
  signup: (payload: SignupRequest) => Promise<void>;
  /** Clear local credentials and revoke the backend refresh session. */
  logout: () => void;
  /** Re-run the "who am I" check (e.g. after external token changes). */
  refresh: () => Promise<void>;
  /** The current authenticated tenant id. Derived from the loaded user's
   *  tenant_id, the active-tenant override (superuser switching), or the
   *  dev DEFAULT_TENANT_ID fallback when no session is loaded yet. */
  tenantId: string;
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<MeResponse | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [sessionError, setSessionError] = useState<string | null>(null);
  const queryClient = useQueryClient();
  const sessionVersion = useRef(0);

  const loadUser = useCallback(async () => {
    const version = ++sessionVersion.current;
    setIsLoading(true);
    try {
      const me = await getMe();
      if (version !== sessionVersion.current) return;
      setUser(me);
      setSessionError(null);
    } catch (err) {
      if (version !== sessionVersion.current) return;
      setUser(null);
      // A 401 here just means "no valid session yet" — that's the normal,
      // expected state for a visitor who hasn't logged in. Anything else
      // (network failure, 5xx, timeout) is a real problem worth surfacing.
      if (err instanceof ApiError) {
        if (err.isAuthError) clearAuthToken();
        setSessionError(err.isAuthError ? null : err.message);
      } else {
        setSessionError("Could not verify your session. Please try again.");
      }
    } finally {
      if (version === sessionVersion.current) setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    // Defer the initial check; loading is already true on first render.
    // Cleanup also prevents a late /me response from updating an unmounted provider.
    let active = true;
    const versionRef = sessionVersion;
    queueMicrotask(() => {
      if (active) void loadUser();
    });
    return () => {
      active = false;
      ++versionRef.current;
    };
  }, [loadUser]);

  const login = useCallback(async (creds: LoginCredentials) => {
    await loginWithCredentials(creds); // throws ApiError on failure
    await loadUser();
  }, [loadUser]);

  const signup = useCallback(async (payload: SignupRequest) => {
    await signupTenant(payload); // throws ApiError on failure
    await loadUser();
  }, [loadUser]);

  const logout = useCallback(() => {
    ++sessionVersion.current;
    void logoutSession().catch(() => {
      setSessionError("Signed out locally, but server session revocation failed.");
    });
    queryClient.clear();
    setUser(null);
    setIsLoading(false);
    setSessionError(null);
    clearActiveTenantOverride();
  }, [queryClient]);

  // Match X-Tenant-Id sent by apiFetch; the backend authorizes overrides.
  const tenantId = getActiveTenantOverride() ?? user?.tenant_id ?? DEFAULT_TENANT_ID;

  const value: AuthContextValue = {
    user,
    isLoading,
    isAuthenticated: user !== null,
    sessionError,
    login,
    signup,
    logout,
    refresh: loadUser,
    tenantId,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error("useAuth() must be called within an <AuthProvider>.");
  }
  return ctx;
}

/**
 * Fetch the current auth token synchronously, for call sites (rare) that
 * need to know "is there a token at all right now" without subscribing to
 * the full context — e.g. a one-off redirect decision outside React.
 */
export function hasStoredToken(): boolean {
  return getAuthToken() !== null;
}

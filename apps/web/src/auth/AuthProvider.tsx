import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { beginSignIn, completeSignIn, getStoredAccessToken, getCurrentUser, isCognitoConfigured, signOut as cognitoSignOut } from "./auth";

interface AuthContextValue {
  loading: boolean; authenticated: boolean; accessToken: string | null; error: Error | null;
  signIn: () => Promise<void>; signOut: () => Promise<void>; getAccessToken: () => Promise<string | null>;
}
const AuthContext = createContext<AuthContextValue | null>(null);
function hasAuthCallback() { const params = new URLSearchParams(window.location.search); return Boolean(params.get("code") || params.get("error")); }

export function AuthProvider({ children }: { children: ReactNode }) {
  const [accessToken, setAccessToken] = useState<string | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const needsAuthResolution = isCognitoConfigured() || hasAuthCallback();
  const [loading, setLoading] = useState(needsAuthResolution);

  useEffect(() => {
    if (!needsAuthResolution) return;
    let cancelled = false;
    void (async () => {
      try {
        const params = new URLSearchParams(window.location.search);
        if (params.get("error")) throw new Error(params.get("error_description") ?? params.get("error") ?? "Sign-in failed");
        const user = hasAuthCallback() ? await completeSignIn() : await getCurrentUser();
        if (!cancelled) { setAccessToken(user && !user.expired ? user.access_token : null); setError(null); }
      } catch (reason: unknown) {
        if (!cancelled) { setAccessToken(null); setError(reason instanceof Error ? reason : new Error("Sign-in failed")); }
      } finally { if (!cancelled) setLoading(false); }
    })();
    return () => { cancelled = true; };
  }, [needsAuthResolution]);

  const signIn = useCallback(async () => { setError(null); await beginSignIn(); }, []);
  const signOut = useCallback(async () => { setAccessToken(null); setError(null); await cognitoSignOut(); }, []);
  const getAccessToken = useCallback(async () => { const token = await getStoredAccessToken(); if (token !== accessToken) setAccessToken(token); return token; }, [accessToken]);
  const value = useMemo<AuthContextValue>(() => ({ loading, authenticated: Boolean(accessToken), accessToken, error, signIn, signOut, getAccessToken }), [accessToken, error, getAccessToken, loading, signIn, signOut]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
export function useAuth() { const context = useContext(AuthContext); if (!context) throw new Error("useAuth must be used inside AuthProvider"); return context; }

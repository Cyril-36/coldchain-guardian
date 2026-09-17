import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { beginSignIn, completeSignIn, getStoredAccessToken, signOut as cognitoSignOut } from "./auth";

interface AuthContextValue {
  loading: boolean;
  authenticated: boolean;
  accessToken: string | null;
  signIn: () => Promise<void>;
  signOut: () => void;
  getAccessToken: () => Promise<string | null>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

function getAuthorizationCode() {
  return new URLSearchParams(window.location.search).get("code");
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [accessToken, setAccessToken] = useState<string | null>(() => getStoredAccessToken());
  const [loading, setLoading] = useState(Boolean(getAuthorizationCode()));

  useEffect(() => {
    const code = getAuthorizationCode();
    if (!code) return;

    let cancelled = false;
    void completeSignIn(code)
      .then((token) => {
        if (!cancelled) setAccessToken(token);
      })
      .catch(() => {
        if (!cancelled) setAccessToken(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => { cancelled = true; };
  }, []);

  const signIn = useCallback(async () => {
    await beginSignIn();
  }, []);

  const signOut = useCallback(() => {
    setAccessToken(null);
    cognitoSignOut();
  }, []);

  const getAccessToken = useCallback(async () => {
    const token = getStoredAccessToken();
    if (token !== accessToken) setAccessToken(token);
    return token;
  }, [accessToken]);

  const value = useMemo<AuthContextValue>(() => ({
    loading,
    authenticated: Boolean(accessToken),
    accessToken,
    signIn,
    signOut,
    getAccessToken,
  }), [accessToken, getAccessToken, loading, signIn, signOut]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used inside AuthProvider");
  return context;
}

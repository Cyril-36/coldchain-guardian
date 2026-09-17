const STORAGE_KEY = "coldchain_guardian_auth";
const PKCE_KEY = "coldchain_guardian_pkce_verifier";
const RETURN_TO_KEY = "coldchain_guardian_return_to";

export interface AuthSession {
  accessToken: string;
  refreshToken?: string;
  expiresAt: number;
}

interface TokenResponse {
  access_token: string;
  expires_in: number;
  refresh_token?: string;
  token_type: string;
}

function config() {
  const authority = import.meta.env.VITE_COGNITO_AUTHORITY?.replace(/\/$/, "");
  const clientId = import.meta.env.VITE_COGNITO_CLIENT_ID;
  const redirectUri = import.meta.env.VITE_COGNITO_REDIRECT_URI ?? window.location.origin;

  if (!authority || !clientId) return null;
  return { authority, clientId, redirectUri };
}

function base64Url(bytes: Uint8Array) {
  let binary = "";
  bytes.forEach((byte) => { binary += String.fromCharCode(byte); });
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

async function createCodeChallenge(verifier: string) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
  return base64Url(new Uint8Array(digest));
}

function randomVerifier() {
  const bytes = crypto.getRandomValues(new Uint8Array(32));
  return base64Url(bytes);
}

function loadSession(): AuthSession | null {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const session = JSON.parse(raw) as AuthSession;
    if (!session.accessToken || session.expiresAt <= Date.now() + 30_000) return null;
    return session;
  } catch {
    return null;
  }
}

function saveSession(session: AuthSession) {
  sessionStorage.setItem(STORAGE_KEY, JSON.stringify(session));
}

function clearSession() {
  sessionStorage.removeItem(STORAGE_KEY);
  sessionStorage.removeItem(PKCE_KEY);
  sessionStorage.removeItem(RETURN_TO_KEY);
}

export function getStoredAccessToken() {
  return loadSession()?.accessToken ?? null;
}

export async function beginSignIn() {
  const settings = config();
  if (!settings) throw new Error("Cognito is not configured");

  const verifier = randomVerifier();
  const challenge = await createCodeChallenge(verifier);
  sessionStorage.setItem(PKCE_KEY, verifier);
  sessionStorage.setItem(RETURN_TO_KEY, `${window.location.pathname}${window.location.search}`);

  const params = new URLSearchParams({
    response_type: "code",
    client_id: settings.clientId,
    redirect_uri: settings.redirectUri,
    scope: "openid email",
    code_challenge_method: "S256",
    code_challenge: challenge,
  });

  window.location.assign(`${settings.authority}/oauth2/authorize?${params.toString()}`);
}

export async function completeSignIn(code: string) {
  const settings = config();
  const verifier = sessionStorage.getItem(PKCE_KEY);
  if (!settings || !verifier) throw new Error("Cognito sign-in session is incomplete");

  const response = await fetch(`${settings.authority}/oauth2/token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "authorization_code",
      client_id: settings.clientId,
      code,
      redirect_uri: settings.redirectUri,
      code_verifier: verifier,
    }),
  });

  const body = await response.json() as Partial<TokenResponse> & { error?: string; error_description?: string };
  if (!response.ok || !body.access_token || !body.expires_in) {
    throw new Error(body.error_description ?? body.error ?? "Cognito token exchange failed");
  }

  saveSession({
    accessToken: body.access_token,
    refreshToken: body.refresh_token,
    expiresAt: Date.now() + body.expires_in * 1000,
  });
  sessionStorage.removeItem(PKCE_KEY);

  const returnTo = sessionStorage.getItem(RETURN_TO_KEY) ?? "/";
  sessionStorage.removeItem(RETURN_TO_KEY);
  window.history.replaceState({}, document.title, returnTo);
  return body.access_token;
}

export function signOut() {
  const settings = config();
  clearSession();

  if (!settings) {
    window.location.assign(window.location.origin);
    return;
  }

  const params = new URLSearchParams({
    client_id: settings.clientId,
    logout_uri: settings.redirectUri,
  });
  window.location.assign(`${settings.authority}/logout?${params.toString()}`);
}

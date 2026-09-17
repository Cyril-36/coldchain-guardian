import { UserManager, WebStorageStateStore, type User, type UserManagerSettings } from "oidc-client-ts";

const RETURN_TO_KEY = "coldchain_guardian_return_to";

function config(): UserManagerSettings | null {
  const authority = import.meta.env.VITE_COGNITO_AUTHORITY?.replace(/\/$/, "");
  const clientId = import.meta.env.VITE_COGNITO_CLIENT_ID;
  const redirectUri = import.meta.env.VITE_COGNITO_REDIRECT_URI ?? window.location.origin;
  if (!authority || !clientId) return null;
  return { authority, client_id: clientId, redirect_uri: redirectUri, post_logout_redirect_uri: redirectUri, response_type: "code", scope: "openid email coldchain/write", userStore: new WebStorageStateStore({ store: window.sessionStorage }), automaticSilentRenew: false };
}
let manager: UserManager | null = null;
function getManager() { const settings = config(); if (!settings) throw new Error("Cognito is not configured"); if (!manager) manager = new UserManager(settings); return manager; }
export async function beginSignIn() { const current = `${window.location.pathname}${window.location.search}`; sessionStorage.setItem(RETURN_TO_KEY, current); await getManager().signinRedirect({ state: current }); }
export async function completeSignIn(): Promise<User> { const user = await getManager().signinCallback(); const returnTo = typeof user.state === "string" && user.state.startsWith("/") ? user.state : sessionStorage.getItem(RETURN_TO_KEY) ?? "/"; sessionStorage.removeItem(RETURN_TO_KEY); window.history.replaceState({}, document.title, returnTo); return user; }
export async function getCurrentUser() { const settings = config(); if (!settings) return null; return getManager().getUser(); }
export async function getStoredAccessToken() { const user = await getCurrentUser(); if (!user || user.expired) return null; return user.access_token; }
export async function signOut() { sessionStorage.removeItem(RETURN_TO_KEY); await getManager().signoutRedirect(); }

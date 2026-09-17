import type { ReactNode } from "react";
import { useAuth } from "./AuthProvider";

function isProtectedUrl() {
  return Boolean(new URLSearchParams(window.location.search).get("run_id"));
}

export function AuthGate({ children }: { children: ReactNode }) {
  const auth = useAuth();
  const protectedUrl = isProtectedUrl();

  if (!protectedUrl) return <>{children}</>;

  if (auth.loading) {
    return <main className="flex min-h-screen items-center justify-center bg-slate-50 px-6 text-slate-950"><section className="w-full max-w-md rounded-xl border border-slate-200 bg-white p-8 text-center shadow-sm"><p className="text-xs font-semibold uppercase tracking-wide text-cyan-700">ColdChain Guardian</p><h1 className="mt-3 text-xl font-semibold">Checking operator session</h1><p className="mt-2 text-sm text-slate-500">Completing secure sign-in…</p></section></main>;
  }

  if (!auth.authenticated) {
    return <main className="flex min-h-screen items-center justify-center bg-slate-50 px-6 text-slate-950"><section className="w-full max-w-md rounded-xl border border-slate-200 bg-white p-8 shadow-sm"><p className="text-xs font-semibold uppercase tracking-wide text-cyan-700">ColdChain Guardian</p><h1 className="mt-3 text-2xl font-semibold">Operator sign-in required</h1><p className="mt-2 text-sm leading-6 text-slate-600">This investigation URL is protected. Sign in with the configured Cognito operator account to view the run and its evidence.</p>{auth.error && <div role="alert" className="mt-4 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs font-medium text-red-800">{auth.error.message}</div>}<button type="button" onClick={() => void auth.signIn()} className="mt-6 w-full rounded-md bg-[#123B5D] px-4 py-3 text-sm font-semibold text-white hover:bg-[#0F304C]">Sign in with Cognito</button><p className="mt-4 text-center text-[11px] text-slate-400">Public demo mode does not require an operator session.</p></section></main>;
  }

  return <div className="min-h-screen bg-slate-50"><div className="border-b border-slate-200 bg-white px-5 py-2"><div className="mx-auto flex max-w-[1440px] items-center justify-end gap-3 text-[11px]"><span className="text-slate-500">Operator session active</span><button type="button" onClick={auth.signOut} className="font-semibold text-[#123B5D] hover:underline">Sign out</button></div></div>{children}</div>;
}

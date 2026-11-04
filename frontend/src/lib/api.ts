"use client";
import { useCallback, useEffect, useState } from "react";

// Authentication uses an httpOnly session cookie set by the backend, so no token is ever stored
// where page scripts can read it. Writes carry a custom header the backend requires (CSRF guard).

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export async function api<T = unknown>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Content-Type", "application/json");
  headers.set("X-Requested-With", "stockintel");
  const r = await fetch(`/api${path}`, { ...init, headers, cache: "no-store", credentials: "same-origin" });
  if (r.status === 401 && typeof window !== "undefined" && !path.startsWith("/auth") && path !== "/me") {
    // eslint-disable-next-line @next/next/no-location-assign-relative-destination -- full reload clears state after session expiry
    window.location.assign("/login");
  }
  if (!r.ok) {
    let msg = r.statusText;
    try {
      const j = await r.json();
      if (typeof j.detail === "string") msg = j.detail;
      else if (Array.isArray(j.detail))
        msg = j.detail.map((d: { loc?: string[]; msg: string }) => `${(d.loc ?? []).filter((x) => x !== "body").join(".")}: ${d.msg}`).join(" · ");
      else msg = JSON.stringify(j.detail ?? j);
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(r.status, msg);
  }
  return r.json() as Promise<T>;
}

export async function logout() {
  try {
    await api("/auth/logout", { method: "POST" });
  } catch {
    /* already logged out */
  }
}

export function useApi<T>(path: string | null, refreshMs?: number) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const load = useCallback(async () => {
    if (!path) return;
    try {
      setData(await api<T>(path));
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [path]);
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- data fetching on mount/interval
    load();
    if (!refreshMs) return;
    const id = setInterval(load, refreshMs);
    return () => clearInterval(id);
  }, [load, refreshMs]);
  return { data, error, loading, reload: load };
}

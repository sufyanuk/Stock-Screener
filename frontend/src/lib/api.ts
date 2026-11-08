"use client";
import { useCallback, useEffect, useState } from "react";

// Personal single-user app: no login. Writes carry a custom header the backend requires (CSRF guard).

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
    // Refresh only while the tab is visible, so a background tab doesn't keep the database awake.
    const id = setInterval(() => { if (document.visibilityState === "visible") load(); }, refreshMs);
    const onShow = () => { if (document.visibilityState === "visible") load(); };
    document.addEventListener("visibilitychange", onShow);
    return () => { clearInterval(id); document.removeEventListener("visibilitychange", onShow); };
  }, [load, refreshMs]);
  return { data, error, loading, reload: load };
}

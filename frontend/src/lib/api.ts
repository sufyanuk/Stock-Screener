"use client";
import { useCallback, useEffect, useState } from "react";

// Personal single-user app: no login. Writes carry a custom header the backend requires (CSRF guard).

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

// ---------------------------------------------------------------- daily response cache
// Market data only changes once a day, so a GET response is reused for the rest of the New York trading
// day. A different filter is a different URL and is fetched once. Any change (scan, import, settings,
// rules — every POST / PUT / DELETE) clears the cache; "reload" always fetches fresh data.
const PREFIX = "apicache:";
const GEN = PREFIX + "gen";
const mem = new Map<string, { day: string; gen: string; data: unknown }>();

// Cache entries belong to one trading day and one deployed build (a new version may change the data shape).
const BUILD = process.env.NEXT_PUBLIC_BUILD_ID ?? "dev";
function nyDay() {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York" }).format(new Date()) + "|" + BUILD;
}
function gen() {
  try { return localStorage.getItem(GEN) ?? "0"; } catch { return "0"; }
}
export function clearApiCache() {
  mem.clear();
  try {
    for (const k of Object.keys(localStorage)) if (k.startsWith(PREFIX) && k !== GEN) localStorage.removeItem(k);
    localStorage.setItem(GEN, String(Date.now()));
  } catch { /* storage unavailable */ }
}
function readCache<T>(path: string): T | undefined {
  const day = nyDay(), g = gen();
  const m = mem.get(path);
  if (m && m.day === day && m.gen === g) return m.data as T;
  try {
    const raw = localStorage.getItem(PREFIX + path);
    if (!raw) return undefined;
    const e = JSON.parse(raw);
    if (e.day !== day || e.gen !== g) { localStorage.removeItem(PREFIX + path); return undefined; }
    mem.set(path, e);
    return e.data as T;
  } catch { return undefined; }
}
function writeCache(path: string, data: unknown) {
  const e = { day: nyDay(), gen: gen(), data };
  mem.set(path, e);
  try {
    localStorage.setItem(PREFIX + path, JSON.stringify(e));
  } catch {
    // full: drop other cached responses (memory copy still serves this tab)
    try { for (const k of Object.keys(localStorage)) if (k.startsWith(PREFIX) && k !== GEN) localStorage.removeItem(k); } catch { /* ignore */ }
  }
}

/** GET with the daily cache (``fresh`` skips it and stores the new response). */
export async function apiCached<T>(path: string, fresh = false): Promise<T> {
  if (!fresh) {
    const hit = readCache<T>(path);
    if (hit !== undefined) return hit;
  }
  const data = await api<T>(path);
  writeCache(path, data);
  return data;
}

export async function api<T = unknown>(path: string, init: RequestInit = {}): Promise<T> {
  const write = !!init.method && init.method.toUpperCase() !== "GET";
  if (write) clearApiCache();
  try {
    return await request<T>(path, init);
  } finally {
    if (write) clearApiCache(); // data changed on the server
  }
}

async function request<T>(path: string, init: RequestInit): Promise<T> {
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

export function useApi<T>(path: string | null) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const fetchData = useCallback(async (fresh: boolean) => {
    if (!path) return;
    try {
      setData(await apiCached<T>(path, fresh));
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [path]);
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- data fetching on mount / filter change (served from the daily cache)
    fetchData(false);
  }, [fetchData]);
  const reload = useCallback(() => fetchData(true), [fetchData]);
  return { data, error, loading, reload };
}

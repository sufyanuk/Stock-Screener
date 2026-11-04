"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { ReactNode, useEffect, useState } from "react";
import { api, logout } from "@/lib/api";

const NAV = [
  ["/", "Dashboard"],
  ["/history", "History"],
  ["/tools", "Calculators"],
  ["/journal", "Journal"],
  ["/methodology", "Methodology"],
  ["/admin", "Admin"],
] as const;

export function Shell({ children }: { children: ReactNode }) {
  const path = usePathname();
  const router = useRouter();
  const [ready, setReady] = useState(false);
  const [menu, setMenu] = useState(false);
  useEffect(() => {
    if (path === "/login" || ready) return;
    // The session cookie is httpOnly, so ask the backend whether we are signed in.
    api("/me").then(() => setReady(true), () => router.replace("/login"));
  }, [path, router, ready]);
  if (path === "/login") return <>{children}</>;
  if (!ready) return null;
  return (
    <div className="min-h-screen">
      <nav className="sticky top-0 z-20 border-b border-line bg-bg/90 backdrop-blur">
        <div className="mx-auto flex max-w-[1500px] items-center gap-4 px-4 py-2.5">
          <Link href="/" className="flex items-center gap-2 font-semibold">
            <span className="grid h-7 w-7 place-items-center rounded-md bg-accent/20 text-accent">SI</span>
            <span>Stock Intel</span>
          </Link>
          <button className="ml-auto md:hidden" onClick={() => setMenu(!menu)} aria-label="menu">☰</button>
          <div className={`${menu ? "flex" : "hidden"} absolute left-0 right-0 top-12 flex-col gap-1 border-b border-line bg-bg p-3 md:static md:flex md:flex-row md:border-0 md:p-0`}>
            {NAV.map(([href, label]) => (
              <Link key={href} href={href} onClick={() => setMenu(false)}
                className={`rounded-md px-2.5 py-1.5 text-sm ${path === href || (href !== "/" && path.startsWith(href)) ? "bg-panel-2 text-ink" : "text-muted hover:text-ink"}`}>
                {label}
              </Link>
            ))}
            <button className="rounded-md px-2.5 py-1.5 text-left text-sm text-muted hover:text-ink md:ml-2"
              onClick={async () => { await logout(); setReady(false); router.replace("/login"); }}>
              Sign out
            </button>
          </div>
        </div>
      </nav>
      <main className="mx-auto max-w-[1500px] px-4 py-5">{children}</main>
      <footer className="mx-auto max-w-[1500px] px-4 pb-8 text-xs text-muted">
        Research tool only — not investment advice. Data may be delayed or incomplete; every value shows its source and timestamp.
        Shariah highlighting (green / red) is an analytical aid, not a fatwa.
      </footer>
    </div>
  );
}

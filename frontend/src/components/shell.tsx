"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { ReactNode, useState } from "react";
import { StockPane } from "@/components/stock-pane";
import { PaneProvider, usePane } from "@/lib/pane-context";

const NAV = [
  ["/", "Dashboard"],
  ["/earnings", "Earnings"],
  ["/seasonality", "Seasonality"],
  ["/history", "History"],
  ["/tools", "Calculators"],
  ["/journal", "Journal"],
  ["/methodology", "Methodology"],
  ["/admin", "Admin"],
] as const;

export function Shell({ children }: { children: ReactNode }) {
  const path = usePathname();
  const [menu, setMenu] = useState(false);
  // Personal app: no sign-in. Access is restricted by Vercel Authentication on the deployment.
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
          </div>
        </div>
      </nav>
      <PaneProvider>
        <MainWithPane>{children}</MainWithPane>
      </PaneProvider>
      <footer className="mx-auto max-w-[1500px] px-4 pb-8 text-xs text-muted">
        Research tool only — not investment advice. Data may be delayed or incomplete; every value shows its source and timestamp.
        Shariah highlighting (green / red) is an analytical aid, not a fatwa.
      </footer>
    </div>
  );
}

/** Content on the left, selected-stock panel on the right (slide-over on small screens). */
function MainWithPane({ children }: { children: ReactNode }) {
  const pane = usePane();
  const open = !!pane?.selected;
  return (
    <main className={`mx-auto px-4 py-5 ${open ? "max-w-[1900px]" : "max-w-[1500px]"}`}>
      <div className={open ? "lg:grid lg:grid-cols-[minmax(0,1fr)_420px] lg:gap-4" : ""}>
        <div className="min-w-0">{children}</div>
        {open && (
          <>
            <div className="fixed inset-0 z-30 bg-black/50 lg:hidden" onClick={() => pane?.select(null)} />
            <div className="fixed inset-y-0 right-0 z-40 w-[min(420px,100vw)] p-2 lg:sticky lg:top-16 lg:z-auto lg:w-auto lg:self-start lg:p-0">
              <StockPane />
            </div>
          </>
        )}
      </div>
    </main>
  );
}

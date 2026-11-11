"use client";
import { useEffect } from "react";
import { Button, Card, Empty, ErrorBox, ShariahLegend, SortTable, Ticker } from "@/components/ui";
import { api, useApi } from "@/lib/api";
import { usd } from "@/lib/format";
import { useBasket } from "@/lib/basket";

type Item = { symbol: string; name: string | null; added_at: string; shariah_status: string | null; price: number | null; price_as_of?: string | null };

export default function BasketPage() {
  const basket = useBasket();
  const { data, error, reload } = useApi<{ items: Item[]; note: string }>("/basket");
  const size = basket?.symbols.size ?? 0;
  // keep this list in step when tickers are added or removed with the hover "+" elsewhere on the page
  useEffect(() => { reload(); }, [size, reload]);
  const rows = (data?.items ?? []).filter((i) => !basket || basket.has(i.symbol));

  async function clear() {
    await api("/basket", { method: "DELETE" });
    basket?.reload();
    reload();
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Basket</h1>
          <p className="text-sm text-muted">
            Tickers you added with the <b>+</b> that appears when you hover over any ticker. They stay here until the next
            nightly scan (weekdays after the US close), then the basket starts empty again.
          </p>
        </div>
        <ShariahLegend />
      </div>
      <ErrorBox error={error} />
      <Card title={`${rows.length} ticker${rows.length === 1 ? "" : "s"} in the basket`}
        right={rows.length > 0 && <Button variant="ghost" onClick={clear}>Clear basket</Button>}>
        {rows.length ? (
          <SortTable rows={rows} rowKey={(r) => r.symbol} initial={null}
            cols={[
              { key: "symbol", label: "Ticker", cell: (r) => <Ticker symbol={r.symbol} status={r.shariah_status} /> },
              { key: "name", label: "Company", cell: (r) => r.name ?? "—" },
              { key: "price", label: "Price", className: "num", cell: (r) => <span title={r.price_as_of ? `Close on ${r.price_as_of}` : undefined}>{usd(r.price)}</span> },
              { key: "added_at", label: "Added", cell: (r) => new Date(r.added_at).toLocaleString() },
              { key: "remove", label: "", cell: (r) => (
                <button className="text-muted hover:text-bear" onClick={() => basket?.toggle(r.symbol)} aria-label={`Remove ${r.symbol}`}>✕ Remove</button>) },
            ]} />
        ) : <Empty>The basket is empty. Hover over any ticker in the app and click <b>+</b> to add it.</Empty>}
      </Card>
    </div>
  );
}

"use client";
import { Card, ErrorBox } from "@/components/ui";
import { useApi } from "@/lib/api";

const TITLES: Record<string, string> = {
  options: "Unusual options activity", insider: "Insider buying", analyst: "Analyst ratings", technical: "Technical support",
  float_liquidity: "Float & liquidity", shariah: "Shariah status (highlight colour only)", ranking: "Eligibility & ranking",
};

export default function MethodologyPage() {
  const { data, error } = useApi<Record<string, string>>("/methodology");
  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">Methodology</h1>
      <p className="text-sm text-muted">This text comes from the code that does the calculation, so it can&apos;t drift out of date.</p>
      <ErrorBox error={error} />
      {data && Object.entries(data).map(([k, v]) => (
        <Card key={k} title={TITLES[k] ?? k}><pre className="whitespace-pre-wrap font-sans text-sm leading-relaxed text-muted">{v}</pre></Card>
      ))}
    </div>
  );
}

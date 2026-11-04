/* eslint-disable @typescript-eslint/no-explicit-any */
export type Criterion = { name: string; outcome: "pass" | "fail" | "missing"; hard: boolean; score: number; explanation: string; metrics: Record<string, any> };

export type Run = {
  id: number; kind: string; session_date: string; status: string; started_at: string; finished_at: string | null;
  funnel: Record<string, any>; errors: string[]; relaxed_criteria: string[]; triggered_by: string;
};

export type Rec = {
  id: number; session_date: string; rank: number; ticker: string; company: string; score: number; price_at_rec: number;
  entry: number | null; stop: number | null; target1: number | null; target2: number | null; is_final: boolean; kind: string;
  day_change_pct: number | null; criteria: Record<string, Criterion>; extra: Record<string, any>;
  revisions: { changed_at: string; changed_by: string; reason: string; before: any; after: any }[];
};

export type ScanRow = {
  ticker: string; company: string; eligible: boolean; score: number; price: number | null; shariah_status: string;
  failures: string[]; criteria: Record<string, Criterion>; extra: Record<string, any>;
};

export type Health = { provider: string; last_success_at: string | null; last_error_at: string | null; last_error: string | null; calls: number; failures: number; state: string };

export type Dashboard = {
  disclaimer: string; market_phase: string; now: string; latest_run: Run | null; top: Rec[]; watchlist: ScanRow[];
  unusual_options: Record<string, any>[]; low_float: Record<string, any>[];
  insider_buys: Record<string, any>[]; provider_health: Health[];
  options_source?: { type: string; current?: boolean; last_completed_session?: string;
    latest?: { id: number; filename: string; session_date: string; rows: number } | null };
};

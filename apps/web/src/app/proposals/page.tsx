"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowRight, Bell, Clock } from "lucide-react";
import { AppShell } from "@/components/AppShell";

interface ProposalListItem {
  id: string;
  symbol: string;
  timeframe: string;
  direction: "BUY" | "SELL";
  entry: string;
  stopLoss: string;
  takeProfit: string;
  volume: string;
  riskAmount: string;
  riskPercent: string;
  rewardRisk: string;
  accountCurrency?: string;
  usdEquivalent: { riskAmount: string };
  aiStatus: string;
  status: string;
  expiresAt: string;
  createdAt: string;
}

const STATUS_FILTERS = [
  "ALL",
  "PENDING_APPROVAL",
  "APPROVED",
  "EXECUTED",
  "REJECTED",
  "EXPIRED",
  "AI_VETOED",
] as const;

function Countdown({ expiresAt, status }: { expiresAt: string; status: string }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (status !== "PENDING_APPROVAL") return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [status]);

  if (status !== "PENDING_APPROVAL") return null;
  const remaining = Math.max(0, Math.floor((Date.parse(expiresAt) - now) / 1000));
  return (
    <span
      className={`inline-flex items-center gap-1 rounded px-2 py-0.5 text-xs font-mono ${
        remaining <= 20
          ? "bg-rose-500/20 text-rose-300"
          : "bg-amber-500/15 text-amber-300"
      }`}
    >
      <Clock className="h-3 w-3" />
      {remaining}s
    </span>
  );
}

export default function ProposalsPage() {
  const [filter, setFilter] = useState<string>("ALL");

  const query = useQuery<{ proposals: ProposalListItem[] }>({
    queryKey: ["proposals", filter],
    queryFn: async () => {
      const qs = filter === "ALL" ? "" : `?status=${filter}`;
      const res = await fetch(`/api/v1/proposals${qs}`, {
        credentials: "include",
      });
      if (!res.ok) throw new Error("Sign in to view trade proposals");
      return res.json() as Promise<{ proposals: ProposalListItem[] }>;
    },
    retry: false,
    refetchInterval: 10_000,
  });

  const enableNotifications = () => {
    if (typeof window !== "undefined" && "Notification" in window) {
      void Notification.requestPermission();
    }
  };

  return (
    <AppShell>
      <div className="mx-auto max-w-6xl px-6 py-8 space-y-6">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <h1 className="text-2xl font-bold">AI Trade Proposals</h1>
            <p className="text-xs text-slate-400">
              Live SSE queue — manual approval required before any MT5 order
            </p>
          </div>
          <button
            type="button"
            onClick={enableNotifications}
            className="inline-flex items-center gap-1.5 rounded-md border border-slate-700 bg-slate-900 px-3 py-1.5 text-xs text-slate-300 hover:bg-slate-800"
          >
            <Bell className="h-3.5 w-3.5 text-amber-400" />
            Enable Browser Alerts
          </button>
        </div>

        <div className="flex flex-wrap gap-1.5">
          {STATUS_FILTERS.map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => setFilter(s)}
              className={`rounded-md px-2.5 py-1 text-xs font-medium transition ${
                filter === s
                  ? "bg-amber-500 text-slate-950"
                  : "bg-slate-900 border border-slate-800 text-slate-400 hover:text-white"
              }`}
            >
              {s.replace("_", " ")}
            </button>
          ))}
        </div>

        {query.isError && (
          <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-6 text-sm text-slate-400">
            Sign in as owner to inspect and approve trade proposals.
          </div>
        )}

        {query.data && query.data.proposals.length === 0 && (
          <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-8 text-center text-sm text-slate-400">
            No proposals matching this filter yet.
          </div>
        )}

        <div className="space-y-3">
          {query.data?.proposals.map((p) => (
            <Link
              key={p.id}
              href={`/proposals/${p.id}`}
              className="block rounded-xl border border-slate-800 bg-slate-900/70 p-4 hover:border-amber-500/40 transition"
            >
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="flex items-center gap-3">
                  <span
                    className={`rounded px-2 py-0.5 text-xs font-bold ${
                      p.direction === "BUY"
                        ? "bg-emerald-500/20 text-emerald-300"
                        : "bg-rose-500/20 text-rose-300"
                    }`}
                  >
                    {p.direction}
                  </span>
                  <span className="font-semibold">
                    {p.symbol} · {p.timeframe}
                  </span>
                  <span className="rounded bg-slate-800 px-2 py-0.5 text-xs text-slate-300">
                    {p.status}
                  </span>
                  {p.aiStatus === "UNAVAILABLE" && (
                    <span className="rounded bg-amber-500/15 px-2 py-0.5 text-xs text-amber-300">
                      AI Unavailable (Deterministic)
                    </span>
                  )}
                  <Countdown expiresAt={p.expiresAt} status={p.status} />
                </div>

                <div className="flex items-center gap-4 text-xs font-mono text-slate-300">
                  <span>Entry: {p.entry}</span>
                  <span>SL: {p.stopLoss}</span>
                  <span>TP: {p.takeProfit}</span>
                  <span>Vol: {p.volume}</span>
                  <span>
                    Risk: {p.riskAmount} {p.accountCurrency ?? "USC"} ($
                    {p.usdEquivalent.riskAmount})
                  </span>
                  <ArrowRight className="h-4 w-4 text-amber-400" />
                </div>
              </div>
            </Link>
          ))}
        </div>
      </div>
    </AppShell>
  );
}

"use client";

import Link from "next/link";
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, Pause, Play, ShieldCheck } from "lucide-react";
import { AppShell } from "@/components/AppShell";

interface DashboardSummary {
  account: {
    login: string;
    server: string;
    mode: "DEMO" | "LIVE";
    accountType: "STANDARD" | "CENT";
    accountCurrency: string;
    currencyScale: number;
    balance: string;
    equity: string;
    freeMargin: string;
    usdEquivalent: {
      balance: string;
      equity: string;
      freeMargin: string;
    };
  } | null;
  controls: {
    isPaused: boolean;
    killSwitchActive: boolean;
  };
  counts: {
    openPositions: number;
    pendingProposals: number;
  };
  risk: {
    daily: {
      lossLimit: string;
      drawdownPercent: number;
      isTripped: boolean;
    } | null;
    weekly: {
      lossLimit: string;
      drawdownPercent: number;
      isTripped: boolean;
      resetAt: string | null;
    } | null;
  };
}

export default function DashboardPage() {
  const queryClient = useQueryClient();
  const [controlMsg, setControlMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const summaryQuery = useQuery<DashboardSummary>({
    queryKey: ["dashboard-summary"],
    queryFn: async () => {
      const res = await fetch("/api/v1/dashboard/summary", {
        credentials: "include",
      });
      if (!res.ok) throw new Error("Unauthenticated");
      return res.json() as Promise<DashboardSummary>;
    },
    retry: false,
    refetchInterval: 20_000,
  });

  const togglePause = async (
    action: "pause" | "resume",
    csrfToken: string | null,
    clearKillSwitch = false,
  ) => {
    if (!csrfToken || busy) return;
    setBusy(true);
    setControlMsg(null);
    try {
      const res = await fetch(`/api/v1/system/${action}`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "x-csrf-token": csrfToken,
        },
        credentials: "include",
        body: JSON.stringify(
          action === "resume" ? { clearKillSwitch } : {},
        ),
      });
      const data = (await res.json().catch(() => ({}))) as { error?: string };
      if (!res.ok) {
        setControlMsg(data.error ?? `Failed to ${action}`);
      } else {
        setControlMsg(
          action === "pause"
            ? "Trading paused (open positions retain SL/TP)."
            : "Trading resumed after checks.",
        );
        void queryClient.invalidateQueries({ queryKey: ["dashboard-summary"] });
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <AppShell>
      {({ latestQuote, csrfToken }) => {
        const s = summaryQuery.data;
        const acc = s?.account;

        return (
          <div className="mx-auto max-w-6xl px-6 py-8 space-y-6">
            <div className="flex flex-wrap items-center justify-between gap-4">
              <div>
                <h1 className="text-2xl font-bold">Trading Dashboard</h1>
                <p className="text-xs text-slate-400">
                  Exness MT5 · Semi-Automatic XAUUSD · Capital-Preservation First
                </p>
              </div>

              {s && csrfToken && (
                <div className="flex items-center gap-2">
                  {!s.controls.isPaused ? (
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => void togglePause("pause", csrfToken)}
                      className="inline-flex items-center gap-1.5 rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-1.5 text-xs font-semibold text-amber-200 hover:bg-amber-500/20"
                    >
                      <Pause className="h-3.5 w-3.5" />
                      Pause Trading
                    </button>
                  ) : (
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() =>
                        void togglePause(
                          "resume",
                          csrfToken,
                          s.controls.killSwitchActive,
                        )
                      }
                      className="inline-flex items-center gap-1.5 rounded-md bg-emerald-500 px-3 py-1.5 text-xs font-semibold text-slate-950 hover:bg-emerald-400"
                    >
                      <Play className="h-3.5 w-3.5" />
                      {s.controls.killSwitchActive
                        ? "Clear Kill Switch & Resume"
                        : "Resume Trading"}
                    </button>
                  )}
                </div>
              )}
            </div>

            {controlMsg && (
              <div className="rounded-lg border border-slate-700 bg-slate-900 px-4 py-2 text-xs text-slate-200">
                {controlMsg}
              </div>
            )}

            {summaryQuery.isError && (
              <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-6 flex items-center justify-between">
                <div className="space-y-1">
                  <p className="font-semibold">Sign in to view account KPIs</p>
                  <p className="text-xs text-slate-400">
                    Protected owner dashboard for Exness MT5 monitoring and
                    semi-automatic trade approval.
                  </p>
                </div>
                <Link
                  href="/login"
                  className="rounded-md bg-amber-500 px-4 py-2 text-sm font-semibold text-slate-950 hover:bg-amber-400"
                >
                  Sign In
                </Link>
              </div>
            )}

            {s && (
              <>
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
                  <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-4">
                    <p className="text-xs text-slate-400">Balance</p>
                    <p className="mt-1 text-xl font-mono font-bold">
                      {acc ? `${acc.balance} ${acc.accountCurrency}` : "—"}
                    </p>
                    {acc && (
                      <p className="text-xs text-slate-400">
                        ≈ ${acc.usdEquivalent.balance} USD
                      </p>
                    )}
                  </div>

                  <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-4">
                    <p className="text-xs text-slate-400">Equity</p>
                    <p className="mt-1 text-xl font-mono font-bold text-emerald-300">
                      {acc ? `${acc.equity} ${acc.accountCurrency}` : "—"}
                    </p>
                    {acc && (
                      <p className="text-xs text-slate-400">
                        ≈ ${acc.usdEquivalent.equity} USD
                      </p>
                    )}
                  </div>

                  <Link
                    href="/proposals"
                    className="rounded-xl border border-slate-800 bg-slate-900/70 p-4 hover:border-amber-500/40 transition"
                  >
                    <p className="text-xs text-slate-400">Pending Approvals</p>
                    <p className="mt-1 text-2xl font-mono font-bold text-amber-300">
                      {s.counts.pendingProposals}
                    </p>
                    <p className="mt-1 inline-flex items-center gap-1 text-xs text-amber-400">
                      Review queue <ArrowRight className="h-3 w-3" />
                    </p>
                  </Link>

                  <Link
                    href="/positions"
                    className="rounded-xl border border-slate-800 bg-slate-900/70 p-4 hover:border-sky-500/40 transition"
                  >
                    <p className="text-xs text-slate-400">Open Positions</p>
                    <p className="mt-1 text-2xl font-mono font-bold text-sky-300">
                      {s.counts.openPositions}
                    </p>
                    <p className="mt-1 inline-flex items-center gap-1 text-xs text-sky-400">
                      Manage / Manual Close <ArrowRight className="h-3 w-3" />
                    </p>
                  </Link>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  {[
                    { label: "Daily Loss Limit", snap: s.risk.daily },
                    { label: "Weekly Loss Limit", snap: s.risk.weekly },
                  ].map(({ label, snap }) => {
                    const dd = snap?.drawdownPercent ?? 0;
                    const limit = Number(snap?.lossLimit ?? 1);
                    const pct = Math.min(100, limit > 0 ? (dd / limit) * 100 : 0);
                    return (
                      <div
                        key={label}
                        className="rounded-xl border border-slate-800 bg-slate-900/70 p-4 space-y-2"
                      >
                        <div className="flex items-center justify-between text-xs">
                          <span className="font-semibold text-slate-300">
                            {label}
                          </span>
                          <span className="font-mono text-slate-400">
                            {dd.toFixed(2)}% / {snap?.lossLimit ?? "—"}%
                            {snap?.isTripped ? " (TRIPPED)" : ""}
                          </span>
                        </div>
                        <div className="h-2 rounded-full bg-slate-800 overflow-hidden">
                          <div
                            className={`h-full transition-all ${
                              snap?.isTripped || pct >= 80
                                ? "bg-rose-500"
                                : "bg-emerald-500"
                            }`}
                            style={{ width: `${pct}%` }}
                          />
                        </div>
                      </div>
                    );
                  })}
                </div>
              </>
            )}

            <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4 flex flex-wrap items-center justify-between gap-4">
              <div className="flex items-center gap-2 text-xs text-slate-300">
                <ShieldCheck className="h-4 w-4 text-emerald-400" />
                <span>
                  {latestQuote
                    ? `Live ${latestQuote.symbol}: Bid ${latestQuote.bid} / Ask ${latestQuote.ask} (Spread ${latestQuote.spread})`
                    : "Awaiting live XAUUSD quote from MT5 worker"}
                </span>
              </div>
              <Link
                href="/market"
                className="text-xs font-semibold text-amber-400 hover:text-amber-300 inline-flex items-center gap-1"
              >
                Open Market Chart <ArrowRight className="h-3.5 w-3.5" />
              </Link>
            </div>
          </div>
        );
      }}
    </AppShell>
  );
}

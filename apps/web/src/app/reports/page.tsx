"use client";

import { useQuery } from "@tanstack/react-query";
import { AppShell } from "@/components/AppShell";

interface PerformanceReport {
  accountCurrency: string;
  currencyScale: number;
  totalTrades: number;
  wins: number;
  losses: number;
  winRatePercent: number;
  netPnl: string;
  netPnlUsd: string;
  grossProfit: string;
  grossLoss: string;
  profitFactor: number | null;
  expectancy: string;
  maxDrawdown: string;
  maxDrawdownUsd: string;
  totalCommission: string;
  totalSwap: string;
  equityCurve: {
    closedAt: string;
    cumulativePnl: number;
    tradeNet: number;
  }[];
}

export default function ReportsPage() {
  const query = useQuery<{ report: PerformanceReport }>({
    queryKey: ["reports"],
    queryFn: async () => {
      const res = await fetch("/api/v1/reports/performance", {
        credentials: "include",
      });
      if (!res.ok) throw new Error("Failed");
      return res.json() as Promise<{ report: PerformanceReport }>;
    },
    retry: false,
  });

  const r = query.data?.report;

  return (
    <AppShell>
      <div className="mx-auto max-w-6xl px-6 py-8 space-y-6">
        <div>
          <h1 className="text-2xl font-bold">Performance Reports</h1>
          <p className="text-xs text-slate-400">
            Reconciled realized metrics including commissions and swaps
          </p>
        </div>

        {r && (
          <>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4 font-mono">
              <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-4">
                <p className="text-xs text-slate-400">Net P/L</p>
                <p
                  className={`mt-1 text-xl font-bold ${
                    Number(r.netPnl) >= 0 ? "text-emerald-400" : "text-rose-400"
                  }`}
                >
                  {r.netPnl} {r.accountCurrency}
                </p>
                <p className="text-xs text-slate-400">≈ ${r.netPnlUsd} USD</p>
              </div>
              <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-4">
                <p className="text-xs text-slate-400">Win Rate</p>
                <p className="mt-1 text-xl font-bold">{r.winRatePercent}%</p>
                <p className="text-xs text-slate-400">
                  {r.wins}W / {r.losses}L ({r.totalTrades} total)
                </p>
              </div>
              <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-4">
                <p className="text-xs text-slate-400">Profit Factor</p>
                <p className="mt-1 text-xl font-bold">
                  {r.profitFactor ?? "—"}
                </p>
                <p className="text-xs text-slate-400">
                  Expectancy: {r.expectancy}
                </p>
              </div>
              <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-4">
                <p className="text-xs text-slate-400">Max Drawdown</p>
                <p className="mt-1 text-xl font-bold text-rose-300">
                  {r.maxDrawdown} {r.accountCurrency}
                </p>
                <p className="text-xs text-slate-400">
                  ≈ ${r.maxDrawdownUsd} USD
                </p>
              </div>
            </div>

            <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-4 text-xs font-mono flex flex-wrap gap-6 text-slate-300">
              <span>Gross Profit: {r.grossProfit}</span>
              <span>Gross Loss: {r.grossLoss}</span>
              <span>Total Commission: {r.totalCommission}</span>
              <span>Total Swap: {r.totalSwap}</span>
            </div>

            <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-5 space-y-3">
              <h2 className="text-sm font-semibold">Equity Curve Points</h2>
              {r.equityCurve.length === 0 ? (
                <p className="text-xs text-slate-400">
                  Complete demo trades to populate the equity curve.
                </p>
              ) : (
                <div className="space-y-1.5 text-xs font-mono">
                  {r.equityCurve.map((pt, i) => (
                    <div
                      key={i}
                      className="flex items-center justify-between border-b border-slate-800/60 py-1"
                    >
                      <span className="text-slate-400">
                        {pt.closedAt.slice(0, 19).replace("T", " ")} UTC
                      </span>
                      <span
                        className={
                          pt.tradeNet >= 0
                            ? "text-emerald-400"
                            : "text-rose-400"
                        }
                      >
                        Trade: {pt.tradeNet >= 0 ? "+" : ""}
                        {pt.tradeNet}
                      </span>
                      <span className="font-semibold">
                        Cumulative: {pt.cumulativePnl} {r.accountCurrency}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </>
        )}
      </div>
    </AppShell>
  );
}

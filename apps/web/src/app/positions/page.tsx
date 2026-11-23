"use client";

import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { XCircle } from "lucide-react";
import { AppShell } from "@/components/AppShell";

interface PositionItem {
  id: string;
  brokerPositionId: string;
  symbol: string;
  side: "BUY" | "SELL";
  volume: string;
  openPrice: string;
  currentPrice: string;
  stopLoss: string | null;
  takeProfit: string | null;
  profit: string;
  commission: string;
  swap: string;
  accountCurrency?: string;
  usdEquivalent: { profit: string; commission: string; swap: string };
  isBotManaged: boolean;
  status: "OPEN" | "CLOSING" | "CLOSED" | "UNKNOWN";
  openedAt: string;
}

export default function OpenPositionsPage() {
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<PositionItem | null>(null);
  const [reason, setReason] = useState("");
  const [closing, setClosing] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);

  const query = useQuery<{ positions: PositionItem[] }>({
    queryKey: ["positions", "OPEN"],
    queryFn: async () => {
      const res = await fetch("/api/v1/positions?status=OPEN", {
        credentials: "include",
      });
      if (!res.ok) throw new Error("Sign in to view open positions");
      return res.json() as Promise<{ positions: PositionItem[] }>;
    },
    retry: false,
    refetchInterval: 5_000,
  });

  const confirmManualClose = async (csrfToken: string | null) => {
    if (!selected || !csrfToken || closing) return;
    setClosing(true);
    setFeedback(null);
    try {
      const idempotencyKey =
        typeof crypto !== "undefined" && "randomUUID" in crypto
          ? crypto.randomUUID()
          : `close-${Date.now()}`;
      const res = await fetch(`/api/v1/positions/${selected.id}/close`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "x-csrf-token": csrfToken,
        },
        credentials: "include",
        body: JSON.stringify({
          idempotencyKey,
          reason: reason.trim() || undefined,
        }),
      });
      const data = (await res.json().catch(() => ({}))) as { error?: string };
      if (!res.ok) {
        setFeedback(data.error ?? "Manual close request failed");
      } else {
        setFeedback(
          `Manual close queued for ticket #${selected.brokerPositionId}. Waiting for MT5 deal reconciliation.`,
        );
        void queryClient.invalidateQueries({ queryKey: ["positions"] });
        void queryClient.invalidateQueries({ queryKey: ["dashboard-summary"] });
      }
    } finally {
      setClosing(false);
      setSelected(null);
      setReason("");
    }
  };

  return (
    <AppShell>
      {({ latestQuote, csrfToken }) => (
        <div className="mx-auto max-w-6xl px-6 py-8 space-y-6">
          <div>
            <h1 className="text-2xl font-bold">Open Positions</h1>
            <p className="text-xs text-slate-400">
              Live MT5 positions · Audited manual close remains enabled even
              when Kill Switch or Pause is active
            </p>
          </div>

          {feedback && (
            <div className="rounded-lg border border-slate-700 bg-slate-900 px-4 py-2.5 text-xs text-slate-200">
              {feedback}
            </div>
          )}

          {query.data && query.data.positions.length === 0 && (
            <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-8 text-center text-sm text-slate-400">
              No open XAUUSD positions right now.
            </div>
          )}

          <div className="space-y-3">
            {query.data?.positions.map((pos) => {
              const pnlNum = Number(pos.profit);
              return (
                <div
                  key={pos.id}
                  className="rounded-xl border border-slate-800 bg-slate-900/70 p-4 flex flex-wrap items-center justify-between gap-4"
                >
                  <div className="space-y-1">
                    <div className="flex items-center gap-2">
                      <span
                        className={`rounded px-2 py-0.5 text-xs font-bold ${
                          pos.side === "BUY"
                            ? "bg-emerald-500/20 text-emerald-300"
                            : "bg-rose-500/20 text-rose-300"
                        }`}
                      >
                        {pos.side}
                      </span>
                      <span className="font-semibold">
                        {pos.symbol} · {pos.volume} lots
                      </span>
                      <span className="text-xs font-mono text-slate-400">
                        #{pos.brokerPositionId}
                      </span>
                      <span className="rounded bg-slate-800 px-2 py-0.5 text-[11px] text-slate-300">
                        {pos.isBotManaged ? "Bot-Managed" : "Manual MT5"}
                      </span>
                    </div>
                    <div className="flex flex-wrap gap-4 text-xs font-mono text-slate-400">
                      <span>Open: {pos.openPrice}</span>
                      <span>Current: {pos.currentPrice}</span>
                      <span>SL: {pos.stopLoss ?? "NONE"}</span>
                      <span>TP: {pos.takeProfit ?? "—"}</span>
                    </div>
                  </div>

                  <div className="flex items-center gap-4">
                    <div className="text-right font-mono">
                      <p
                        className={`text-sm font-bold ${
                          pnlNum >= 0 ? "text-emerald-400" : "text-rose-400"
                        }`}
                      >
                        {pos.profit} {pos.accountCurrency ?? "USC"}
                      </p>
                      <p className="text-xs text-slate-400">
                        ≈ ${pos.usdEquivalent.profit} USD
                      </p>
                    </div>

                    <button
                      type="button"
                      disabled={pos.status !== "OPEN" || !csrfToken}
                      onClick={() => setSelected(pos)}
                      className="inline-flex items-center gap-1.5 rounded-md border border-rose-500/50 bg-rose-500/15 px-3 py-1.5 text-xs font-semibold text-rose-200 hover:bg-rose-500/25 disabled:opacity-40"
                    >
                      <XCircle className="h-4 w-4" />
                      {pos.status === "CLOSING" ? "Closing..." : "Close Position"}
                    </button>
                  </div>
                </div>
              );
            })}
          </div>

          {selected && (
            <div className="fixed inset-0 z-50 bg-black/70 flex items-center justify-center p-4">
              <div className="max-w-md w-full rounded-xl border border-slate-700 bg-slate-900 p-6 space-y-4">
                <h2 className="text-lg font-bold">
                  Confirm Manual Close (#{selected.brokerPositionId})
                </h2>
                <div className="rounded-lg bg-slate-950 border border-slate-800 p-3 text-xs font-mono space-y-1">
                  <p>
                    Position: {selected.side} {selected.volume} {selected.symbol}
                  </p>
                  <p>
                    Floating P/L: {selected.profit}{" "}
                    {selected.accountCurrency ?? "USC"} ($
                    {selected.usdEquivalent.profit} USD)
                  </p>
                  {latestQuote && (
                    <p>
                      Live Quote: {latestQuote.bid} / {latestQuote.ask} (Spread{" "}
                      {latestQuote.spread})
                    </p>
                  )}
                </div>

                <div className="space-y-1">
                  <label className="text-xs text-slate-400" htmlFor="close-reason">
                    Reason (optional, saved to Audit Log)
                  </label>
                  <input
                    id="close-reason"
                    value={reason}
                    onChange={(e) => setReason(e.target.value)}
                    placeholder="e.g. Manual profit lock ahead of session close"
                    className="w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-xs"
                  />
                </div>

                <div className="flex justify-end gap-2">
                  <button
                    type="button"
                    onClick={() => setSelected(null)}
                    className="rounded-md border border-slate-700 px-3 py-1.5 text-xs text-slate-300"
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    disabled={closing}
                    onClick={() => void confirmManualClose(csrfToken)}
                    className="rounded-md bg-rose-600 px-4 py-1.5 text-xs font-semibold text-white hover:bg-rose-500 disabled:opacity-50"
                  >
                    {closing ? "Sending Close..." : "Confirm Market Close"}
                  </button>
                </div>
              </div>
            </div>
          )}
        </div>
      )}
    </AppShell>
  );
}

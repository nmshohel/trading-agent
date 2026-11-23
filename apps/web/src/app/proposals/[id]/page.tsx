"use client";

import { useParams, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, Clock, XCircle } from "lucide-react";
import { AppShell } from "@/components/AppShell";

interface ProposalDetail {
  id: string;
  symbol: string;
  timeframe: string;
  strategyName?: string;
  direction: "BUY" | "SELL";
  entry: string;
  stopLoss: string;
  takeProfit: string;
  volume: string;
  riskAmount: string;
  riskPercent: string;
  rewardRisk: string;
  estimatedMargin: string | null;
  spreadAtProposal: string;
  accountCurrency?: string;
  usdEquivalent: {
    riskAmount: string;
    estimatedMargin: string | null;
  };
  rationale: string;
  aiStatus: "COMPLETED" | "VETOED" | "UNAVAILABLE" | "SKIPPED";
  aiRawResponse: {
    confidence?: number;
    market_regime?: string;
    reasons?: string[];
    risks?: string[];
    invalidation_notes?: string[];
  } | null;
  status: string;
  expiresAt: string;
  createdAt: string;
}

export default function ProposalDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const queryClient = useQueryClient();
  const id = params.id;

  const [now, setNow] = useState(() => Date.now());
  const [busyAction, setBusyAction] = useState<"approve" | "reject" | null>(
    null,
  );
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const idempotencyKeyRef = useRef<string>(
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID()
      : `idem-${Date.now()}`,
  );

  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);

  const query = useQuery<{ proposal: ProposalDetail }>({
    queryKey: ["proposal", id],
    queryFn: async () => {
      const res = await fetch(`/api/v1/proposals/${id}`, {
        credentials: "include",
      });
      if (!res.ok) throw new Error("Unable to load proposal");
      return res.json() as Promise<{ proposal: ProposalDetail }>;
    },
    retry: false,
  });

  const handleDecision = async (
    decision: "approve" | "reject",
    csrfToken: string | null,
  ) => {
    if (!csrfToken || busyAction) return;
    setBusyAction(decision);
    setActionMessage(null);
    try {
      const res = await fetch(`/api/v1/proposals/${id}/${decision}`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "x-csrf-token": csrfToken,
        },
        credentials: "include",
        body: JSON.stringify({
          idempotencyKey: idempotencyKeyRef.current,
        }),
      });
      const data = (await res.json().catch(() => ({}))) as { error?: string };
      if (!res.ok) {
        setActionMessage(data.error ?? `Failed to ${decision} proposal`);
      } else {
        setActionMessage(
          decision === "approve"
            ? "Approved and queued for MT5 pre-trade revalidation & execution."
            : "Proposal rejected. Post-rejection cooldown activated.",
        );
        void queryClient.invalidateQueries({ queryKey: ["proposal", id] });
        void queryClient.invalidateQueries({ queryKey: ["proposals"] });
      }
    } finally {
      setBusyAction(null);
    }
  };

  return (
    <AppShell>
      {({ latestQuote, csrfToken, accountMode, accountType }) => {
        const p = query.data?.proposal;
        const remainingSeconds = p
          ? Math.max(0, Math.floor((Date.parse(p.expiresAt) - now) / 1000))
          : 0;
        const canAct =
          p?.status === "PENDING_APPROVAL" &&
          remainingSeconds > 0 &&
          Boolean(csrfToken);

        return (
          <div className="mx-auto max-w-4xl px-6 py-8 space-y-6">
            <button
              type="button"
              onClick={() => router.push("/proposals")}
              className="text-xs text-slate-400 hover:text-white"
            >
              ← Back to Proposals
            </button>

            {query.isLoading && (
              <p className="text-sm text-slate-400">Loading proposal...</p>
            )}

            {p && (
              <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-6 space-y-6">
                <div className="flex flex-wrap items-center justify-between gap-4 border-b border-slate-800 pb-4">
                  <div className="space-y-1">
                    <div className="flex items-center gap-2">
                      <span
                        className={`rounded px-2.5 py-0.5 text-xs font-bold ${
                          p.direction === "BUY"
                            ? "bg-emerald-500/20 text-emerald-300"
                            : "bg-rose-500/20 text-rose-300"
                        }`}
                      >
                        {p.direction}
                      </span>
                      <h1 className="text-xl font-bold">
                        {p.symbol} ({p.timeframe})
                      </h1>
                      <span className="rounded bg-slate-800 px-2 py-0.5 text-xs text-slate-300">
                        {p.status}
                      </span>
                    </div>
                    <p className="text-xs text-slate-400">
                      Strategy: {p.strategyName ?? "xauusd_v1"} · Account:{" "}
                      {accountMode} / {accountType}
                    </p>
                  </div>

                  {p.status === "PENDING_APPROVAL" && (
                    <div className="inline-flex items-center gap-1.5 rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-1.5 font-mono text-sm text-amber-200">
                      <Clock className="h-4 w-4" />
                      Expires in {remainingSeconds}s
                    </div>
                  )}
                </div>

                {p.aiStatus === "UNAVAILABLE" && (
                  <div className="flex items-center gap-2 rounded-lg border border-amber-500/40 bg-amber-500/10 px-4 py-2.5 text-xs text-amber-200">
                    <AlertTriangle className="h-4 w-4 shrink-0" />
                    AI explanation was unavailable during generation. Showing
                    deterministic strategy summary.
                  </div>
                )}

                <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 font-mono text-sm">
                  <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-3">
                    <p className="text-xs text-slate-400">Proposed Entry</p>
                    <p className="mt-1 font-bold">{p.entry}</p>
                  </div>
                  <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-3">
                    <p className="text-xs text-slate-400">Stop Loss</p>
                    <p className="mt-1 font-bold text-rose-400">{p.stopLoss}</p>
                  </div>
                  <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-3">
                    <p className="text-xs text-slate-400">Take Profit</p>
                    <p className="mt-1 font-bold text-emerald-400">
                      {p.takeProfit}
                    </p>
                  </div>
                  <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-3">
                    <p className="text-xs text-slate-400">Calculated Volume</p>
                    <p className="mt-1 font-bold text-amber-300">
                      {p.volume} lots
                    </p>
                  </div>
                </div>

                <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 text-sm">
                  <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-3">
                    <p className="text-xs text-slate-400">
                      Estimated Max Loss
                    </p>
                    <p className="mt-1 font-mono font-bold text-rose-300">
                      {p.riskAmount} {p.accountCurrency ?? "USC"} ($
                      {p.usdEquivalent.riskAmount} · {p.riskPercent}%)
                    </p>
                  </div>
                  <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-3">
                    <p className="text-xs text-slate-400">
                      Reward / Risk &amp; Margin
                    </p>
                    <p className="mt-1 font-mono">
                      1:{p.rewardRisk} · Margin: {p.estimatedMargin ?? "—"}
                    </p>
                  </div>
                  <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-3">
                    <p className="text-xs text-slate-400">
                      Live Bid / Ask (Spread)
                    </p>
                    <p className="mt-1 font-mono">
                      {latestQuote
                        ? `${latestQuote.bid} / ${latestQuote.ask} (${latestQuote.spread})`
                        : `Proposal spread: ${p.spreadAtProposal}`}
                    </p>
                  </div>
                </div>

                <div className="space-y-3 text-sm">
                  <div>
                    <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-400">
                      Rationale
                    </h2>
                    <p className="mt-1 text-slate-200">{p.rationale}</p>
                  </div>

                  {p.aiRawResponse?.risks && (
                    <div>
                      <h2 className="text-xs font-semibold uppercase tracking-wider text-amber-400">
                        Risk Warnings
                      </h2>
                      <ul className="mt-1 list-disc list-inside text-slate-300 space-y-0.5">
                        {p.aiRawResponse.risks.map((r, i) => (
                          <li key={i}>{r}</li>
                        ))}
                      </ul>
                    </div>
                  )}

                  {p.aiRawResponse?.invalidation_notes && (
                    <div>
                      <h2 className="text-xs font-semibold uppercase tracking-wider text-rose-400">
                        Invalidation Conditions
                      </h2>
                      <ul className="mt-1 list-disc list-inside text-slate-300 space-y-0.5">
                        {p.aiRawResponse.invalidation_notes.map((n, i) => (
                          <li key={i}>{n}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>

                {actionMessage && (
                  <div className="rounded-lg border border-slate-700 bg-slate-950 px-4 py-2.5 text-xs text-slate-200">
                    {actionMessage}
                  </div>
                )}

                <div className="flex flex-wrap items-center justify-end gap-3 pt-2 border-t border-slate-800">
                  <button
                    type="button"
                    disabled={!canAct || busyAction !== null}
                    onClick={() => void handleDecision("reject", csrfToken)}
                    className="inline-flex items-center gap-1.5 rounded-md border border-slate-700 px-4 py-2 text-sm font-medium text-slate-200 hover:bg-slate-800 disabled:opacity-40"
                  >
                    <XCircle className="h-4 w-4 text-rose-400" />
                    {busyAction === "reject" ? "Rejecting..." : "Reject"}
                  </button>
                  <button
                    type="button"
                    disabled={!canAct || busyAction !== null}
                    onClick={() => void handleDecision("approve", csrfToken)}
                    className="inline-flex items-center gap-1.5 rounded-md bg-emerald-500 px-5 py-2 text-sm font-semibold text-slate-950 hover:bg-emerald-400 disabled:opacity-40"
                  >
                    <CheckCircle2 className="h-4 w-4" />
                    {busyAction === "approve"
                      ? "Approving..."
                      : "Approve & Queue Execution"}
                  </button>
                </div>
              </div>
            )}
          </div>
        );
      }}
    </AppShell>
  );
}

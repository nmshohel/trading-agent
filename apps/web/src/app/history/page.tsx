"use client";

import { useQuery } from "@tanstack/react-query";
import { AppShell } from "@/components/AppShell";

interface ClosedPosition {
  id: string;
  brokerPositionId: string;
  symbol: string;
  side: "BUY" | "SELL";
  volume: string;
  openPrice: string;
  closePrice: string | null;
  profit: string;
  commission: string;
  swap: string;
  accountCurrency?: string;
  usdEquivalent: { profit: string };
  closedAt: string | null;
}

interface OrderItem {
  id: string;
  actionType: "OPEN" | "CLOSE";
  symbol: string;
  side: "BUY" | "SELL";
  volume: string;
  entryPrice: string | null;
  fillingMode: string | null;
  retcode: number | null;
  brokerComment: string | null;
  status: string;
  createdAt: string;
}

export default function TradeHistoryPage() {
  const closedQuery = useQuery<{ positions: ClosedPosition[] }>({
    queryKey: ["positions", "CLOSED"],
    queryFn: async () => {
      const res = await fetch("/api/v1/positions?status=CLOSED&limit=100", {
        credentials: "include",
      });
      if (!res.ok) throw new Error("Failed");
      return res.json() as Promise<{ positions: ClosedPosition[] }>;
    },
    retry: false,
  });

  const ordersQuery = useQuery<{ orders: OrderItem[] }>({
    queryKey: ["orders"],
    queryFn: async () => {
      const res = await fetch("/api/v1/orders?limit=100", {
        credentials: "include",
      });
      if (!res.ok) throw new Error("Failed");
      return res.json() as Promise<{ orders: OrderItem[] }>;
    },
    retry: false,
  });

  return (
    <AppShell>
      <div className="mx-auto max-w-6xl px-6 py-8 space-y-8">
        <div>
          <h1 className="text-2xl font-bold">Trade History</h1>
          <p className="text-xs text-slate-400">
            Reconciled closed positions, broker retcodes, commissions, and swaps
          </p>
        </div>

        <section className="space-y-3">
          <h2 className="text-sm font-semibold text-slate-300">
            Closed Positions
          </h2>
          <div className="overflow-x-auto rounded-xl border border-slate-800 bg-slate-900/60">
            <table className="w-full text-left text-xs font-mono">
              <thead className="border-b border-slate-800 text-slate-400">
                <tr>
                  <th className="px-4 py-2.5">Ticket</th>
                  <th className="px-4 py-2.5">Side</th>
                  <th className="px-4 py-2.5">Vol</th>
                  <th className="px-4 py-2.5">Open / Close</th>
                  <th className="px-4 py-2.5">Comm / Swap</th>
                  <th className="px-4 py-2.5">Profit</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/70">
                {(closedQuery.data?.positions ?? []).map((p) => (
                  <tr key={p.id}>
                    <td className="px-4 py-2.5">#{p.brokerPositionId}</td>
                    <td className="px-4 py-2.5">{p.side}</td>
                    <td className="px-4 py-2.5">{p.volume}</td>
                    <td className="px-4 py-2.5">
                      {p.openPrice} → {p.closePrice ?? "—"}
                    </td>
                    <td className="px-4 py-2.5">
                      {p.commission} / {p.swap}
                    </td>
                    <td
                      className={`px-4 py-2.5 font-bold ${
                        Number(p.profit) >= 0
                          ? "text-emerald-400"
                          : "text-rose-400"
                      }`}
                    >
                      {p.profit} {p.accountCurrency ?? "USC"} ($
                      {p.usdEquivalent.profit})
                    </td>
                  </tr>
                ))}
                {(closedQuery.data?.positions ?? []).length === 0 && (
                  <tr>
                    <td
                      colSpan={6}
                      className="px-4 py-6 text-center text-slate-500"
                    >
                      No closed trades yet.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </section>

        <section className="space-y-3">
          <h2 className="text-sm font-semibold text-slate-300">
            Broker Orders &amp; Retcodes
          </h2>
          <div className="overflow-x-auto rounded-xl border border-slate-800 bg-slate-900/60">
            <table className="w-full text-left text-xs font-mono">
              <thead className="border-b border-slate-800 text-slate-400">
                <tr>
                  <th className="px-4 py-2.5">Time (UTC)</th>
                  <th className="px-4 py-2.5">Action</th>
                  <th className="px-4 py-2.5">Side / Vol</th>
                  <th className="px-4 py-2.5">Fill</th>
                  <th className="px-4 py-2.5">Retcode</th>
                  <th className="px-4 py-2.5">Status</th>
                  <th className="px-4 py-2.5">Comment</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/70">
                {(ordersQuery.data?.orders ?? []).map((o) => (
                  <tr key={o.id}>
                    <td className="px-4 py-2.5">
                      {o.createdAt.slice(0, 19).replace("T", " ")}
                    </td>
                    <td className="px-4 py-2.5">{o.actionType}</td>
                    <td className="px-4 py-2.5">
                      {o.side} {o.volume}
                    </td>
                    <td className="px-4 py-2.5">{o.entryPrice ?? "—"}</td>
                    <td className="px-4 py-2.5">{o.retcode ?? "—"}</td>
                    <td className="px-4 py-2.5">{o.status}</td>
                    <td className="px-4 py-2.5 text-slate-400">
                      {o.brokerComment ?? "—"}
                    </td>
                  </tr>
                ))}
                {(ordersQuery.data?.orders ?? []).length === 0 && (
                  <tr>
                    <td
                      colSpan={7}
                      className="px-4 py-6 text-center text-slate-500"
                    >
                      No orders submitted yet.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </section>
      </div>
    </AppShell>
  );
}

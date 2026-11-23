"use client";

import { useQuery } from "@tanstack/react-query";
import { AppShell } from "@/components/AppShell";

interface DetailedHealth {
  status: string;
  timestamp: string;
  services: {
    database: string;
    redis: string;
    worker: string;
    mt5: string;
  };
  workerDetails: {
    workerId: string;
    heartbeatAgeSeconds: number | null;
    lastHeartbeatAt: string;
    terminalTradeAllowed: boolean;
    accountTradeAllowed: boolean;
    accountMode: string | null;
    symbol: string | null;
    serverTimeOffsetSeconds: number | null;
  } | null;
}

export default function SystemHealthPage() {
  const query = useQuery<DetailedHealth>({
    queryKey: ["system-health"],
    queryFn: async () => {
      const res = await fetch("/api/v1/system/health", {
        credentials: "include",
      });
      if (!res.ok) throw new Error("Failed");
      return res.json() as Promise<DetailedHealth>;
    },
    retry: false,
    refetchInterval: 10_000,
  });

  const h = query.data;

  return (
    <AppShell>
      {({ sseStatus }) => (
        <div className="mx-auto max-w-5xl px-6 py-8 space-y-6">
          <div>
            <h1 className="text-2xl font-bold">System Health</h1>
            <p className="text-xs text-slate-400">
              Live MT5 terminal status, Algo Trading permission, worker
              heartbeat, Redis, SSE, and PostgreSQL
            </p>
          </div>

          <div className="grid grid-cols-2 sm:grid-cols-5 gap-3 text-xs">
            {[
              { label: "PostgreSQL", val: h?.services.database ?? "—" },
              { label: "Redis", val: h?.services.redis ?? "—" },
              { label: "Worker", val: h?.services.worker ?? "—" },
              { label: "MT5 Terminal", val: h?.services.mt5 ?? "—" },
              { label: "SSE Stream", val: sseStatus },
            ].map((item) => (
              <div
                key={item.label}
                className="rounded-xl border border-slate-800 bg-slate-900/70 p-4"
              >
                <p className="text-slate-400">{item.label}</p>
                <p className="mt-1 text-sm font-mono font-bold uppercase">
                  {item.val}
                </p>
              </div>
            ))}
          </div>

          <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-5 space-y-2 text-xs font-mono">
            <h2 className="text-sm font-sans font-semibold mb-2">
              Windows MT5 Worker Diagnostics
            </h2>
            {h?.workerDetails ? (
              <>
                <p>Worker ID: {h.workerDetails.workerId}</p>
                <p>
                  Heartbeat Age: {h.workerDetails.heartbeatAgeSeconds ?? "—"}s
                  (last: {h.workerDetails.lastHeartbeatAt})
                </p>
                <p>
                  MT5 Algo Trading Enabled:{" "}
                  {h.workerDetails.terminalTradeAllowed ? "YES" : "NO"}
                </p>
                <p>
                  Account Trading Permitted:{" "}
                  {h.workerDetails.accountTradeAllowed ? "YES" : "NO"}
                </p>
                <p>
                  Account Mode: {h.workerDetails.accountMode ?? "—"} · Symbol:{" "}
                  {h.workerDetails.symbol ?? "—"}
                </p>
                <p>
                  Broker Server UTC Offset:{" "}
                  {h.workerDetails.serverTimeOffsetSeconds ?? 0}s
                </p>
              </>
            ) : (
              <p className="text-slate-400">
                No worker heartbeat received yet. Start `apps/worker` on the
                Windows MT5 host.
              </p>
            )}
          </div>
        </div>
      )}
    </AppShell>
  );
}

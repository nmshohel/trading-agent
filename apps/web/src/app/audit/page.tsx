"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { AppShell } from "@/components/AppShell";

interface AuditItem {
  id: string;
  actorEmail?: string | null;
  action: string;
  entityType: string;
  entityId: string | null;
  metadata: Record<string, unknown>;
  ip: string | null;
  createdAt: string;
}

export default function AuditLogsPage() {
  const [filterAction, setFilterAction] = useState("");

  const query = useQuery<{ logs: AuditItem[] }>({
    queryKey: ["audit-logs", filterAction],
    queryFn: async () => {
      const qs = filterAction.trim()
        ? `?action=${encodeURIComponent(filterAction.trim())}`
        : "";
      const res = await fetch(`/api/v1/audit-logs${qs}`, {
        credentials: "include",
      });
      if (!res.ok) throw new Error("Failed");
      return res.json() as Promise<{ logs: AuditItem[] }>;
    },
    retry: false,
  });

  return (
    <AppShell>
      <div className="mx-auto max-w-6xl px-6 py-8 space-y-6">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <h1 className="text-2xl font-bold">Audit Logs</h1>
            <p className="text-xs text-slate-400">
              Immutable record of approvals, rejections, manual closes, settings
              changes, weekly resets, and kill switches
            </p>
          </div>
          <input
            value={filterAction}
            onChange={(e) => setFilterAction(e.target.value)}
            placeholder="Filter by exact action (e.g. PROPOSAL_APPROVED)"
            className="w-72 rounded-md border border-slate-700 bg-slate-900 px-3 py-1.5 text-xs"
          />
        </div>

        <div className="overflow-x-auto rounded-xl border border-slate-800 bg-slate-900/60">
          <table className="w-full text-left text-xs font-mono">
            <thead className="border-b border-slate-800 text-slate-400">
              <tr>
                <th className="px-4 py-2.5">Timestamp (UTC)</th>
                <th className="px-4 py-2.5">Action</th>
                <th className="px-4 py-2.5">Actor</th>
                <th className="px-4 py-2.5">Entity</th>
                <th className="px-4 py-2.5">Metadata</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/70">
              {(query.data?.logs ?? []).map((log) => (
                <tr key={log.id}>
                  <td className="px-4 py-2.5 whitespace-nowrap text-slate-400">
                    {log.createdAt.slice(0, 19).replace("T", " ")}
                  </td>
                  <td className="px-4 py-2.5 font-semibold text-amber-300">
                    {log.action}
                  </td>
                  <td className="px-4 py-2.5">{log.actorEmail ?? "system"}</td>
                  <td className="px-4 py-2.5">
                    {log.entityType}
                    {log.entityId ? `:${log.entityId.slice(0, 8)}` : ""}
                  </td>
                  <td className="px-4 py-2.5 text-slate-400 max-w-md truncate">
                    {JSON.stringify(log.metadata)}
                  </td>
                </tr>
              ))}
              {(query.data?.logs ?? []).length === 0 && (
                <tr>
                  <td
                    colSpan={5}
                    className="px-4 py-6 text-center text-slate-500"
                  >
                    No audit records found.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </AppShell>
  );
}

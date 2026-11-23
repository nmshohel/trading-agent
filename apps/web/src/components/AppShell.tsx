"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Activity, OctagonAlert, Radio, X } from "lucide-react";
import {
  useTradingStream,
  type LiveAlertEvent,
  type LiveHealthEvent,
  type LiveQuoteEvent,
  type SseConnectionState,
} from "@/hooks/useTradingStream";

interface MeResponse {
  user: { id: string; email: string; role: string };
  csrfToken: string;
}

interface AccountResponse {
  account: {
    mode: "DEMO" | "LIVE";
    accountType: "STANDARD" | "CENT";
    accountCurrency: string;
    balance: string;
    equity: string;
    usdEquivalent: {
      balance: string;
      equity: string;
      freeMargin: string;
    };
  } | null;
}

interface SystemHealthResponse {
  status: string;
  services: {
    database: string;
    redis: string;
    worker: string;
    mt5: string;
  };
}

interface SettingsBadgeResponse {
  settings: {
    demoAutoExecute?: boolean;
  } | null;
}

export interface ShellStreamContext {
  sseStatus: SseConnectionState;
  latestQuote: LiveQuoteEvent | null;
  latestHealth: LiveHealthEvent | null;
  latestAlert: LiveAlertEvent | null;
  csrfToken: string | null;
  accountMode: "DEMO" | "LIVE";
  accountType: "STANDARD" | "CENT";
  demoAutoExecute: boolean;
}

const NAV_ITEMS = [
  { href: "/", label: "Dashboard" },
  { href: "/market", label: "Market" },
  { href: "/proposals", label: "Proposals" },
  { href: "/positions", label: "Positions" },
  { href: "/history", label: "History" },
  { href: "/settings", label: "Settings" },
  { href: "/reports", label: "Reports" },
  { href: "/health", label: "Health" },
  { href: "/audit", label: "Audit" },
] as const;

export function AppShell({
  children,
}: {
  children: ReactNode | ((ctx: ShellStreamContext) => ReactNode);
}) {
  const pathname = usePathname();
  const queryClient = useQueryClient();
  const [confirmKill, setConfirmKill] = useState(false);
  const [killBusy, setKillBusy] = useState(false);
  const [killFeedback, setKillFeedback] = useState<string | null>(null);

  const {
    status: sseStatus,
    latestQuote,
    latestHealth,
    latestAlert,
    dismissAlert,
  } = useTradingStream(true);

  const meQuery = useQuery<MeResponse>({
    queryKey: ["auth-me"],
    queryFn: async () => {
      const res = await fetch("/api/v1/auth/me", { credentials: "include" });
      if (!res.ok) throw new Error("Unauthenticated");
      return res.json() as Promise<MeResponse>;
    },
    retry: false,
  });

  const accountQuery = useQuery<AccountResponse>({
    queryKey: ["account"],
    queryFn: async () => {
      const res = await fetch("/api/v1/account", { credentials: "include" });
      if (!res.ok) throw new Error("Unauthenticated");
      return res.json() as Promise<AccountResponse>;
    },
    retry: false,
  });

  const healthQuery = useQuery<SystemHealthResponse>({
    queryKey: ["system-health"],
    queryFn: async () => {
      const res = await fetch("/api/v1/system/health", {
        credentials: "include",
      });
      if (!res.ok) throw new Error("Unauthenticated");
      return res.json() as Promise<SystemHealthResponse>;
    },
    retry: false,
    refetchInterval: 30_000,
  });

  const settingsQuery = useQuery<SettingsBadgeResponse>({
    queryKey: ["settings"],
    queryFn: async () => {
      const res = await fetch("/api/v1/settings/trading", {
        credentials: "include",
      });
      if (!res.ok) throw new Error("Unauthenticated");
      return res.json() as Promise<SettingsBadgeResponse>;
    },
    retry: false,
    refetchInterval: 30_000,
  });

  const accountMode =
    latestHealth?.accountMode ?? accountQuery.data?.account?.mode ?? "DEMO";
  const accountType = accountQuery.data?.account?.accountType ?? "CENT";
  const demoAutoExecute =
    accountMode === "DEMO" && Boolean(settingsQuery.data?.settings?.demoAutoExecute);
  const mt5Connected =
    latestHealth?.mt5Connected ??
    healthQuery.data?.services.mt5 === "connected";
  const csrfToken = meQuery.data?.csrfToken ?? null;

  const triggerKillSwitch = async () => {
    if (!csrfToken || killBusy) return;
    setKillBusy(true);
    setKillFeedback(null);
    try {
      const res = await fetch("/api/v1/system/kill-switch", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "x-csrf-token": csrfToken,
        },
        credentials: "include",
        body: JSON.stringify({ reason: "Header emergency kill switch" }),
      });
      const data = (await res.json().catch(() => ({}))) as {
        cancelledProposalsCount?: number;
        error?: string;
      };
      if (!res.ok) {
        setKillFeedback(data.error ?? "Kill switch request failed");
      } else {
        setKillFeedback(
          `Kill switch activated (${data.cancelledProposalsCount ?? 0} proposals cancelled).`,
        );
        void queryClient.invalidateQueries({ queryKey: ["proposals"] });
        void queryClient.invalidateQueries({ queryKey: ["dashboard-summary"] });
        void queryClient.invalidateQueries({ queryKey: ["system-health"] });
      }
    } finally {
      setKillBusy(false);
      setConfirmKill(false);
    }
  };

  const sseBadgeColor =
    sseStatus === "connected"
      ? "bg-emerald-500/15 text-emerald-300 border-emerald-500/30"
      : sseStatus === "reconnecting" || sseStatus === "connecting"
        ? "bg-amber-500/15 text-amber-300 border-amber-500/30"
        : "bg-rose-500/15 text-rose-300 border-rose-500/30";

  return (
    <div className="min-h-screen flex flex-col">
      <header className="sticky top-0 z-20 border-b border-slate-800 bg-slate-900/95 backdrop-blur px-4 py-2.5 flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-3">
          <Link href="/" className="flex items-center gap-2 font-semibold text-sm">
            <Activity className="h-4 w-4 text-amber-400" />
            <span>AI Forex Agent</span>
          </Link>

          <nav className="flex flex-wrap items-center gap-1 text-xs">
            {NAV_ITEMS.map((item) => {
              const active =
                item.href === "/"
                  ? pathname === "/"
                  : pathname.startsWith(item.href);
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={`rounded-md px-2 py-1 transition ${
                    active
                      ? "bg-slate-800 text-white font-medium"
                      : "text-slate-400 hover:text-white"
                  }`}
                >
                  {item.label}
                </Link>
              );
            })}
          </nav>
        </div>

        <div className="flex flex-wrap items-center gap-2 text-xs">
          <span
            className={`rounded px-2 py-0.5 font-semibold border ${
              accountMode === "LIVE"
                ? "bg-rose-500/20 text-rose-300 border-rose-500/40"
                : "bg-emerald-500/15 text-emerald-300 border-emerald-500/30"
            }`}
          >
            {accountMode}
          </span>

          {demoAutoExecute && (
            <span className="rounded bg-violet-500/20 px-2 py-0.5 font-semibold text-violet-200 border border-violet-400/40">
              DEMO AUTO
            </span>
          )}

          <span className="rounded bg-sky-500/15 px-2 py-0.5 font-medium text-sky-300 border border-sky-500/30">
            {accountType}
          </span>

          <span
            className={`inline-flex items-center gap-1 rounded px-2 py-0.5 border ${sseBadgeColor}`}
          >
            <Radio className="h-3 w-3" />
            SSE: {sseStatus}
          </span>

          <span
            className={`inline-flex items-center gap-1.5 rounded px-2 py-0.5 border ${
              mt5Connected
                ? "bg-emerald-500/10 text-emerald-300 border-emerald-500/30"
                : "bg-slate-800 text-slate-400 border-slate-700"
            }`}
          >
            <span
              className={`h-2 w-2 rounded-full ${
                mt5Connected ? "bg-emerald-400" : "bg-slate-500"
              }`}
            />
            MT5: {mt5Connected ? "Connected" : "Offline"}
          </span>

          <button
            type="button"
            disabled={!csrfToken}
            onClick={() => setConfirmKill(true)}
            className="inline-flex items-center gap-1 rounded-md border border-rose-500/50 bg-rose-500/20 px-2.5 py-1 font-semibold text-rose-200 hover:bg-rose-500/30 disabled:opacity-40 transition"
          >
            <OctagonAlert className="h-3.5 w-3.5" />
            Kill Switch
          </button>

          {!meQuery.data && (
            <Link
              href="/login"
              className="rounded-md bg-amber-500 px-3 py-1 font-medium text-slate-950 hover:bg-amber-400 transition"
            >
              Sign In
            </Link>
          )}
        </div>
      </header>

      {(killFeedback || latestAlert) && (
        <div className="bg-amber-500/15 border-b border-amber-500/30 px-6 py-2 text-xs text-amber-100 flex items-center justify-between gap-4">
          <span>{killFeedback ?? latestAlert?.message}</span>
          <button
            type="button"
            onClick={() => {
              setKillFeedback(null);
              dismissAlert();
            }}
            className="text-amber-200 hover:text-white"
            aria-label="Dismiss alert"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      )}

      {confirmKill && (
        <div className="fixed inset-0 z-50 bg-black/70 flex items-center justify-center p-4">
          <div className="max-w-md w-full rounded-xl border border-rose-500/40 bg-slate-900 p-6 space-y-4">
            <h2 className="text-lg font-bold text-rose-300">
              Activate Emergency Kill Switch?
            </h2>
            <p className="text-sm text-slate-300">
              This immediately blocks all new orders and cancels pending or
              queued approvals. Open positions keep their broker Stop Loss &amp;
              Take Profit and remain manually closeable.
            </p>
            <div className="flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setConfirmKill(false)}
                className="rounded-md border border-slate-700 px-3 py-1.5 text-sm text-slate-300 hover:bg-slate-800"
              >
                Cancel
              </button>
              <button
                type="button"
                disabled={killBusy}
                onClick={() => void triggerKillSwitch()}
                className="rounded-md bg-rose-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-rose-500 disabled:opacity-50"
              >
                {killBusy ? "Activating..." : "Confirm Kill Switch"}
              </button>
            </div>
          </div>
        </div>
      )}

      <main className="flex-1">
        {typeof children === "function"
          ? children({
              sseStatus,
              latestQuote,
              latestHealth,
              latestAlert,
              csrfToken,
              accountMode,
              accountType,
              demoAutoExecute,
            })
          : children}
      </main>
    </div>
  );
}

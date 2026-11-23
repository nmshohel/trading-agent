"use client";

import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Trash2, Plus, RotateCcw } from "lucide-react";
import { AppShell } from "@/components/AppShell";

interface TradingSettings {
  id: string;
  symbol: string;
  riskPercent: string;
  dailyLossLimit: string;
  weeklyLossLimit: string;
  maxSpread: string;
  maxDeviationPoints: number;
  commissionPerLot: string;
  approvalExpirySeconds: number;
  postRejectionCooldownMinutes: number;
  aiFallbackMode: "DETERMINISTIC_ONLY" | "SKIP";
  demoAutoExecute?: boolean;
}

interface StrategyItem {
  id: string;
  name: string;
  version: string;
  timeframe: string;
  isActive: boolean;
}

interface BlackoutItem {
  id: string;
  title: string;
  reason: string | null;
  startsAt: string;
  endsAt: string;
  isActive: boolean;
}

export default function SettingsPage() {
  const queryClient = useQueryClient();
  const [msg, setMsg] = useState<string | null>(null);
  const [resetNote, setResetNote] = useState("");
  const [boTitle, setBoTitle] = useState("");
  const [boStart, setBoStart] = useState("");
  const [boEnd, setBoEnd] = useState("");
  const [riskPatch, setRiskPatch] = useState<Partial<TradingSettings>>({});

  const settingsQuery = useQuery<{ settings: TradingSettings | null }>({
    queryKey: ["settings"],
    queryFn: async () => {
      const res = await fetch("/api/v1/settings/trading", {
        credentials: "include",
      });
      if (!res.ok) throw new Error("Failed");
      return res.json() as Promise<{ settings: TradingSettings | null }>;
    },
    retry: false,
  });

  const strategiesQuery = useQuery<{ strategies: StrategyItem[] }>({
    queryKey: ["strategies"],
    queryFn: async () => {
      const res = await fetch("/api/v1/strategies", { credentials: "include" });
      if (!res.ok) throw new Error("Failed");
      return res.json() as Promise<{ strategies: StrategyItem[] }>;
    },
    retry: false,
  });

  const blackoutsQuery = useQuery<{ blackouts: BlackoutItem[] }>({
    queryKey: ["blackouts"],
    queryFn: async () => {
      const res = await fetch("/api/v1/blackouts", { credentials: "include" });
      if (!res.ok) throw new Error("Failed");
      return res.json() as Promise<{ blackouts: BlackoutItem[] }>;
    },
    retry: false,
  });

  const saveSettings = async (csrfToken: string | null) => {
    if (!csrfToken || Object.keys(riskPatch).length === 0) return;
    setMsg(null);
    const res = await fetch("/api/v1/settings/trading", {
      method: "PATCH",
      headers: {
        "Content-Type": "application/json",
        "x-csrf-token": csrfToken,
      },
      credentials: "include",
      body: JSON.stringify(riskPatch),
    });
    const data = (await res.json().catch(() => ({}))) as { error?: string };
    if (!res.ok) {
      setMsg(data.error ?? "Failed to save settings");
    } else {
      setMsg("Trading settings saved & audited.");
      setRiskPatch({});
      void queryClient.invalidateQueries({ queryKey: ["settings"] });
    }
  };

  const resetWeekly = async (csrfToken: string | null) => {
    if (!csrfToken) return;
    setMsg(null);
    const res = await fetch("/api/v1/risk/weekly/reset", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "x-csrf-token": csrfToken,
      },
      credentials: "include",
      body: JSON.stringify({ note: resetNote }),
    });
    const data = (await res.json().catch(() => ({}))) as { error?: string };
    setMsg(
      res.ok
        ? "Weekly loss limit reset and logged to Audit Log."
        : (data.error ?? "Weekly reset failed"),
    );
    if (res.ok) {
      setResetNote("");
      void queryClient.invalidateQueries({ queryKey: ["dashboard-summary"] });
    }
  };

  const toggleStrategy = async (s: StrategyItem, csrfToken: string | null) => {
    if (!csrfToken) return;
    await fetch(`/api/v1/strategies/${s.id}`, {
      method: "PATCH",
      headers: {
        "Content-Type": "application/json",
        "x-csrf-token": csrfToken,
      },
      credentials: "include",
      body: JSON.stringify({ isActive: !s.isActive }),
    });
    void queryClient.invalidateQueries({ queryKey: ["strategies"] });
  };

  const addBlackout = async (csrfToken: string | null) => {
    if (!csrfToken || !boTitle || !boStart || !boEnd) return;
    setMsg(null);
    const res = await fetch("/api/v1/blackouts", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "x-csrf-token": csrfToken,
      },
      credentials: "include",
      body: JSON.stringify({
        title: boTitle,
        startsAt: new Date(boStart).toISOString(),
        endsAt: new Date(boEnd).toISOString(),
      }),
    });
    const data = (await res.json().catch(() => ({}))) as { error?: string };
    if (!res.ok) {
      setMsg(data.error ?? "Failed to create blackout window");
    } else {
      setBoTitle("");
      setBoStart("");
      setBoEnd("");
      setMsg("Blackout window created.");
      void queryClient.invalidateQueries({ queryKey: ["blackouts"] });
    }
  };

  const deleteBlackout = async (id: string, csrfToken: string | null) => {
    if (!csrfToken) return;
    await fetch(`/api/v1/blackouts/${id}`, {
      method: "DELETE",
      headers: { "x-csrf-token": csrfToken },
      credentials: "include",
    });
    void queryClient.invalidateQueries({ queryKey: ["blackouts"] });
  };

  const toggleDemoAutoExecute = async (
    nextValue: boolean,
    csrfToken: string | null,
  ) => {
    if (!csrfToken) return;
    setMsg(null);
    const res = await fetch("/api/v1/settings/trading", {
      method: "PATCH",
      headers: {
        "Content-Type": "application/json",
        "x-csrf-token": csrfToken,
      },
      credentials: "include",
      body: JSON.stringify({ demoAutoExecute: nextValue }),
    });
    const data = (await res.json().catch(() => ({}))) as { error?: string };
    if (!res.ok) {
      setMsg(data.error ?? "Failed to update Demo Auto-Execute");
    } else {
      setMsg(
        nextValue
          ? "Demo Auto-Execute ENABLED (DEMO ONLY — Real/LIVE accounts remain strictly manual)."
          : "Demo Auto-Execute DISABLED (Manual approval required for all trades).",
      );
      void queryClient.invalidateQueries({ queryKey: ["settings"] });
      void queryClient.invalidateQueries({ queryKey: ["dashboard-summary"] });
    }
  };

  return (
    <AppShell>
      {({ csrfToken, accountMode }) => {
        const s = settingsQuery.data?.settings;
        const isLive = accountMode === "LIVE";
        const autoDemoOn = !isLive && Boolean(s?.demoAutoExecute);
        return (
          <div className="mx-auto max-w-5xl px-6 py-8 space-y-8">
            <div>
              <h1 className="text-2xl font-bold">
                Risk, Strategy &amp; Blackout Settings
              </h1>
              <p className="text-xs text-slate-400">
                Strict v1 safety bounds enforced server-side (risk ≤ 0.50%, max
                positions = 1, mandatory SL)
              </p>
            </div>

            {msg && (
              <div className="rounded-lg border border-slate-700 bg-slate-900 px-4 py-2 text-xs text-slate-200">
                {msg}
              </div>
            )}

            {s && (
              <section className="rounded-xl border border-violet-500/30 bg-violet-950/20 p-5 space-y-3">
                <div className="flex flex-wrap items-center justify-between gap-4">
                  <div className="space-y-1">
                    <div className="flex items-center gap-2">
                      <h2 className="text-sm font-semibold text-white">
                        Demo Auto-Execute (DEMO ONLY)
                      </h2>
                      <span
                        className={`rounded px-2 py-0.5 text-[11px] font-semibold border ${
                          isLive
                            ? "bg-rose-500/20 text-rose-300 border-rose-500/40"
                            : autoDemoOn
                              ? "bg-violet-500/25 text-violet-200 border-violet-400/50"
                              : "bg-slate-800 text-slate-300 border-slate-700"
                        }`}
                      >
                        {isLive
                          ? "LOCKED OFF (LIVE)"
                          : autoDemoOn
                            ? "AUTO DEMO ON"
                            : "MANUAL APPROVAL"}
                      </span>
                    </div>
                    <p className="text-xs text-slate-300 max-w-2xl">
                      When enabled on a <strong>DEMO</strong> account, valid M15
                      strategy setups are automatically approved and executed
                      with mandatory Stop Loss, Take Profit, and full risk
                      validation. <strong>LIVE accounts are hard-locked</strong>{" "}
                      to manual human approval and automatically disable this
                      option.
                    </p>
                  </div>
                  <button
                    type="button"
                    disabled={!csrfToken || isLive}
                    onClick={() =>
                      void toggleDemoAutoExecute(!s.demoAutoExecute, csrfToken)
                    }
                    className={`rounded-md px-4 py-2 text-xs font-semibold transition disabled:opacity-40 ${
                      autoDemoOn
                        ? "bg-rose-500/20 text-rose-200 border border-rose-500/40 hover:bg-rose-500/30"
                        : "bg-violet-500 text-white hover:bg-violet-400"
                    }`}
                  >
                    {isLive
                      ? "Disabled on LIVE"
                      : autoDemoOn
                        ? "Turn OFF (Require Approval)"
                        : "Turn ON (Auto-Trade DEMO)"}
                  </button>
                </div>
              </section>
            )}

            {s && (
              <section className="rounded-xl border border-slate-800 bg-slate-900/70 p-5 space-y-4">
                <h2 className="text-sm font-semibold">Risk Parameters</h2>
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 text-xs">
                  <label className="space-y-1">
                    <span className="text-slate-400">
                      Risk % per trade (0.05 – 0.50)
                    </span>
                    <input
                      defaultValue={s.riskPercent}
                      onChange={(e) =>
                        setRiskPatch((p) => ({
                          ...p,
                          riskPercent: e.target.value,
                        }))
                      }
                      className="w-full rounded border border-slate-700 bg-slate-950 px-3 py-1.5 font-mono"
                    />
                  </label>
                  <label className="space-y-1">
                    <span className="text-slate-400">Max Spread</span>
                    <input
                      defaultValue={s.maxSpread}
                      onChange={(e) =>
                        setRiskPatch((p) => ({
                          ...p,
                          maxSpread: e.target.value,
                        }))
                      }
                      className="w-full rounded border border-slate-700 bg-slate-950 px-3 py-1.5 font-mono"
                    />
                  </label>
                  <label className="space-y-1">
                    <span className="text-slate-400">
                      Commission / Lot (account currency)
                    </span>
                    <input
                      defaultValue={s.commissionPerLot}
                      onChange={(e) =>
                        setRiskPatch((p) => ({
                          ...p,
                          commissionPerLot: e.target.value,
                        }))
                      }
                      className="w-full rounded border border-slate-700 bg-slate-950 px-3 py-1.5 font-mono"
                    />
                  </label>
                  <label className="space-y-1">
                    <span className="text-slate-400">
                      Max Deviation (points)
                    </span>
                    <input
                      type="number"
                      defaultValue={s.maxDeviationPoints}
                      onChange={(e) =>
                        setRiskPatch((p) => ({
                          ...p,
                          maxDeviationPoints: Number(e.target.value),
                        }))
                      }
                      className="w-full rounded border border-slate-700 bg-slate-950 px-3 py-1.5 font-mono"
                    />
                  </label>
                  <label className="space-y-1">
                    <span className="text-slate-400">
                      Approval Expiry (seconds)
                    </span>
                    <input
                      type="number"
                      defaultValue={s.approvalExpirySeconds}
                      onChange={(e) =>
                        setRiskPatch((p) => ({
                          ...p,
                          approvalExpirySeconds: Number(e.target.value),
                        }))
                      }
                      className="w-full rounded border border-slate-700 bg-slate-950 px-3 py-1.5 font-mono"
                    />
                  </label>
                  <label className="space-y-1">
                    <span className="text-slate-400">AI Fallback Mode</span>
                    <select
                      defaultValue={s.aiFallbackMode}
                      onChange={(e) =>
                        setRiskPatch((p) => ({
                          ...p,
                          aiFallbackMode: e.target.value as
                            | "DETERMINISTIC_ONLY"
                            | "SKIP",
                        }))
                      }
                      className="w-full rounded border border-slate-700 bg-slate-950 px-3 py-1.5"
                    >
                      <option value="DETERMINISTIC_ONLY">
                        DETERMINISTIC_ONLY
                      </option>
                      <option value="SKIP">SKIP</option>
                    </select>
                  </label>
                </div>
                <button
                  type="button"
                  disabled={!csrfToken}
                  onClick={() => void saveSettings(csrfToken)}
                  className="rounded-md bg-amber-500 px-4 py-1.5 text-xs font-semibold text-slate-950 hover:bg-amber-400 disabled:opacity-40"
                >
                  Save Settings
                </button>
              </section>
            )}

            <section className="rounded-xl border border-slate-800 bg-slate-900/70 p-5 space-y-3">
              <h2 className="text-sm font-semibold">
                Weekly Loss Limit Manual Review &amp; Reset
              </h2>
              <p className="text-xs text-slate-400">
                If the 2.0% weekly loss limit trips, enter a review note (min 10
                characters) to audit and reset.
              </p>
              <div className="flex flex-wrap gap-2">
                <input
                  value={resetNote}
                  onChange={(e) => setResetNote(e.target.value)}
                  placeholder="Document review findings before resetting weekly lock..."
                  className="flex-1 min-w-[260px] rounded border border-slate-700 bg-slate-950 px-3 py-1.5 text-xs"
                />
                <button
                  type="button"
                  disabled={!csrfToken || resetNote.trim().length < 10}
                  onClick={() => void resetWeekly(csrfToken)}
                  className="inline-flex items-center gap-1.5 rounded-md border border-amber-500/50 bg-amber-500/15 px-3 py-1.5 text-xs font-semibold text-amber-200 disabled:opacity-40"
                >
                  <RotateCcw className="h-3.5 w-3.5" />
                  Reset Weekly Lock
                </button>
              </div>
            </section>

            <section className="rounded-xl border border-slate-800 bg-slate-900/70 p-5 space-y-3">
              <h2 className="text-sm font-semibold">Strategies</h2>
              <div className="space-y-2">
                {(strategiesQuery.data?.strategies ?? []).map((st) => (
                  <div
                    key={st.id}
                    className="flex items-center justify-between rounded-lg border border-slate-800 bg-slate-950/60 px-4 py-2.5 text-xs"
                  >
                    <div>
                      <span className="font-semibold">
                        {st.name} v{st.version}
                      </span>{" "}
                      <span className="text-slate-400">({st.timeframe})</span>
                    </div>
                    <button
                      type="button"
                      disabled={!csrfToken}
                      onClick={() => void toggleStrategy(st, csrfToken)}
                      className={`rounded px-3 py-1 font-semibold ${
                        st.isActive
                          ? "bg-emerald-500/20 text-emerald-300"
                          : "bg-slate-800 text-slate-400"
                      }`}
                    >
                      {st.isActive ? "Active" : "Disabled"}
                    </button>
                  </div>
                ))}
              </div>
            </section>

            <section className="rounded-xl border border-slate-800 bg-slate-900/70 p-5 space-y-4">
              <h2 className="text-sm font-semibold">
                News Blackout Windows (UTC / Local)
              </h2>
              <div className="grid grid-cols-1 sm:grid-cols-4 gap-2 text-xs">
                <input
                  value={boTitle}
                  onChange={(e) => setBoTitle(e.target.value)}
                  placeholder="Event title (e.g. US NFP)"
                  className="rounded border border-slate-700 bg-slate-950 px-3 py-1.5"
                />
                <input
                  type="datetime-local"
                  value={boStart}
                  onChange={(e) => setBoStart(e.target.value)}
                  className="rounded border border-slate-700 bg-slate-950 px-3 py-1.5"
                />
                <input
                  type="datetime-local"
                  value={boEnd}
                  onChange={(e) => setBoEnd(e.target.value)}
                  className="rounded border border-slate-700 bg-slate-950 px-3 py-1.5"
                />
                <button
                  type="button"
                  disabled={!csrfToken || !boTitle || !boStart || !boEnd}
                  onClick={() => void addBlackout(csrfToken)}
                  className="inline-flex items-center justify-center gap-1 rounded bg-sky-500 px-3 py-1.5 font-semibold text-slate-950 hover:bg-sky-400 disabled:opacity-40"
                >
                  <Plus className="h-3.5 w-3.5" />
                  Add Window
                </button>
              </div>

              <div className="space-y-2">
                {(blackoutsQuery.data?.blackouts ?? []).map((b) => (
                  <div
                    key={b.id}
                    className="flex items-center justify-between rounded-lg border border-slate-800 bg-slate-950/60 px-4 py-2 text-xs"
                  >
                    <div>
                      <span className="font-semibold">{b.title}</span>
                      <span className="ml-3 font-mono text-slate-400">
                        {b.startsAt.slice(0, 16)} → {b.endsAt.slice(0, 16)} UTC
                      </span>
                    </div>
                    <button
                      type="button"
                      disabled={!csrfToken}
                      onClick={() => void deleteBlackout(b.id, csrfToken)}
                      className="text-rose-400 hover:text-rose-300"
                      aria-label="Delete blackout"
                    >
                      <Trash2 className="h-4 w-4" />
                    </button>
                  </div>
                ))}
              </div>
            </section>
          </div>
        );
      }}
    </AppShell>
  );
}

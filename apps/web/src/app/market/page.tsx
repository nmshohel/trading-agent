"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle } from "lucide-react";
import { AppShell } from "@/components/AppShell";
import {
  CandlestickChart,
  type ChartCandle,
} from "@/components/CandlestickChart";

type Timeframe = "M5" | "M15" | "H1";

interface MarketSummaryResponse {
  quote: {
    symbol: string;
    bid: string;
    ask: string;
    spread: string;
    quoteTime: string;
  } | null;
  isStale: boolean;
  stalenessSeconds: number | null;
}

interface CandlesResponse {
  symbol: string;
  timeframe: Timeframe;
  count: number;
  candles: ChartCandle[];
}

export default function MarketOverviewPage() {
  const [timeframe, setTimeframe] = useState<Timeframe>("M15");

  const marketQuery = useQuery<MarketSummaryResponse>({
    queryKey: ["market-xauusd"],
    queryFn: async () => {
      const res = await fetch("/api/v1/market/xauusd", {
        credentials: "include",
      });
      if (!res.ok) throw new Error("Failed to load market summary");
      return res.json() as Promise<MarketSummaryResponse>;
    },
    retry: false,
    refetchInterval: 5_000,
  });

  const candlesQuery = useQuery<CandlesResponse>({
    queryKey: ["market-candles", timeframe],
    queryFn: async () => {
      const res = await fetch(
        `/api/v1/market/xauusd/candles?timeframe=${timeframe}&limit=200`,
        { credentials: "include" },
      );
      if (!res.ok) throw new Error("Failed to load candles");
      return res.json() as Promise<CandlesResponse>;
    },
    retry: false,
    refetchInterval: 15_000,
  });

  return (
    <AppShell>
      {({ latestQuote }) => {
        const activeQuote = latestQuote ?? marketQuery.data?.quote ?? null;
        const isStale = latestQuote ? false : (marketQuery.data?.isStale ?? true);

        return (
          <div className="mx-auto max-w-6xl px-6 py-8 space-y-6">
            <div className="flex flex-wrap items-center justify-between gap-4">
              <div>
                <h1 className="text-2xl font-bold">
                  Market Overview — {activeQuote?.symbol ?? "XAUUSDc"}
                </h1>
                <p className="text-xs text-slate-400">
                  Live quotes via SSE · Historical UTC closed candles
                </p>
              </div>

              <div className="flex items-center gap-1 rounded-lg border border-slate-800 bg-slate-900 p-1">
                {(["M5", "M15", "H1"] as const).map((tf) => (
                  <button
                    key={tf}
                    type="button"
                    onClick={() => setTimeframe(tf)}
                    className={`rounded-md px-3 py-1 text-xs font-semibold transition ${
                      timeframe === tf
                        ? "bg-amber-500 text-slate-950"
                        : "text-slate-400 hover:text-white"
                    }`}
                  >
                    {tf}
                  </button>
                ))}
              </div>
            </div>

            {isStale && (
              <div className="flex items-center gap-2 rounded-lg border border-amber-500/40 bg-amber-500/10 px-4 py-2.5 text-xs text-amber-200">
                <AlertTriangle className="h-4 w-4 shrink-0" />
                Market quote is stale or awaiting live MT5 worker stream. New
                orders are blocked while data is stale.
              </div>
            )}

            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
              <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-4">
                <p className="text-xs text-slate-400">Bid</p>
                <p className="mt-1 text-2xl font-mono font-bold text-emerald-400">
                  {activeQuote?.bid ?? "—"}
                </p>
              </div>
              <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-4">
                <p className="text-xs text-slate-400">Ask</p>
                <p className="mt-1 text-2xl font-mono font-bold text-rose-400">
                  {activeQuote?.ask ?? "—"}
                </p>
              </div>
              <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-4">
                <p className="text-xs text-slate-400">Spread</p>
                <p className="mt-1 text-2xl font-mono font-bold text-amber-300">
                  {activeQuote?.spread ?? "—"}
                </p>
              </div>
            </div>

            <CandlestickChart candles={candlesQuery.data?.candles ?? []} />
          </div>
        );
      }}
    </AppShell>
  );
}

"use client";

import { useEffect, useRef } from "react";
import {
  createChart,
  ColorType,
  type CandlestickData,
  type UTCTimestamp,
} from "lightweight-charts";

export interface ChartCandle {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
}

export function CandlestickChart({ candles }: { candles: ChartCandle[] }) {
  const containerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    const chart = createChart(el, {
      layout: {
        background: { type: ColorType.Solid, color: "#0f172a" },
        textColor: "#94a3b8",
      },
      grid: {
        vertLines: { color: "#1e293b" },
        horzLines: { color: "#1e293b" },
      },
      width: el.clientWidth,
      height: 420,
      timeScale: {
        timeVisible: true,
        secondsVisible: false,
      },
    });

    const series = chart.addCandlestickSeries({
      upColor: "#10b981",
      downColor: "#f43f5e",
      borderVisible: false,
      wickUpColor: "#10b981",
      wickDownColor: "#f43f5e",
    });

    const data: CandlestickData[] = candles.map((c) => ({
      time: c.time as UTCTimestamp,
      open: c.open,
      high: c.high,
      low: c.low,
      close: c.close,
    }));
    series.setData(data);
    chart.timeScale().fitContent();

    const observer = new ResizeObserver((entries) => {
      for (const entry of entries) {
        chart.applyOptions({ width: Math.floor(entry.contentRect.width) });
      }
    });
    observer.observe(el);

    return () => {
      observer.disconnect();
      chart.remove();
    };
  }, [candles]);

  if (candles.length === 0) {
    return (
      <div className="h-[420px] rounded-xl border border-slate-800 bg-slate-900/60 flex items-center justify-center text-sm text-slate-400">
        No closed candle snapshots yet. Start the MT5 worker to stream XAUUSD
        candles.
      </div>
    );
  }

  return (
    <div
      ref={containerRef}
      className="w-full rounded-xl border border-slate-800 overflow-hidden"
    />
  );
}

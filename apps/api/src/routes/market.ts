import { Router, type RequestHandler } from "express";
import { z } from "zod";

export const CandlesQuerySchema = z.object({
  timeframe: z.enum(["M5", "M15", "H1"]).default("M15"),
  limit: z.coerce.number().int().min(1).max(500).default(200),
  symbol: z.string().min(1).optional(),
});

export interface LatestQuoteRecord {
  symbol: string;
  bid: string;
  ask: string;
  spread: string;
  quoteTime: string;
  indicators?: Record<string, unknown>;
}

export interface CandleRecord {
  symbol: string;
  timeframe: string;
  candleTime: string;
  open: string;
  high: string;
  low: string;
  close: string;
  volume: string;
  bid: string;
  ask: string;
  spread: string;
  indicators: Record<string, unknown>;
}

export interface MarketQueryRepository {
  getLatestQuote(symbol?: string): Promise<LatestQuoteRecord | null>;
  getCandles(input: {
    symbol?: string;
    timeframe: "M5" | "M15" | "H1";
    limit: number;
  }): Promise<CandleRecord[]>;
}

const QUOTE_STALE_MS = 15_000;

export function createMarketRouter(opts: {
  repo: MarketQueryRepository;
  requireAuth: RequestHandler;
}) {
  const router = Router();

  router.get(
    "/api/v1/market/xauusd",
    opts.requireAuth,
    async (req, res, next) => {
      try {
        const symbol =
          typeof req.query.symbol === "string" ? req.query.symbol : undefined;
        const quote = await opts.repo.getLatestQuote(symbol);
        if (!quote) {
          res.status(200).json({
            quote: null,
            isStale: true,
            stalenessSeconds: null,
          });
          return;
        }

        const quoteMs = Date.parse(quote.quoteTime);
        const ageMs = Number.isFinite(quoteMs)
          ? Math.max(0, Date.now() - quoteMs)
          : Number.POSITIVE_INFINITY;

        res.status(200).json({
          quote,
          isStale: ageMs > QUOTE_STALE_MS,
          stalenessSeconds: Number.isFinite(ageMs)
            ? Math.floor(ageMs / 1000)
            : null,
        });
      } catch (err) {
        next(err);
      }
    },
  );

  router.get(
    "/api/v1/market/xauusd/candles",
    opts.requireAuth,
    async (req, res, next) => {
      try {
        const parsed = CandlesQuerySchema.parse(req.query);
        const rows = await opts.repo.getCandles(parsed);

        const candles = rows.map((c) => ({
          time: Math.floor(Date.parse(c.candleTime) / 1000),
          candleTime: c.candleTime,
          open: Number(c.open),
          high: Number(c.high),
          low: Number(c.low),
          close: Number(c.close),
          volume: Number(c.volume),
          spread: c.spread,
          indicators: c.indicators,
        }));

        res.status(200).json({
          symbol: rows[0]?.symbol ?? parsed.symbol ?? "XAUUSDc",
          timeframe: parsed.timeframe,
          count: candles.length,
          candles,
        });
      } catch (err) {
        next(err);
      }
    },
  );

  return router;
}

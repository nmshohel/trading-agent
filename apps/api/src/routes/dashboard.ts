import { Router, type RequestHandler } from "express";
import { z } from "zod";

const ReportsQuerySchema = z.object({
  from: z.string().datetime().optional(),
  to: z.string().datetime().optional(),
});

const AuditLogsQuerySchema = z.object({
  action: z.string().optional(),
  limit: z.coerce.number().int().min(1).max(200).default(100),
});

export interface DashboardSummaryData {
  account: {
    login: string;
    server: string;
    mode: "DEMO" | "LIVE";
    accountType: "STANDARD" | "CENT";
    accountCurrency: string;
    currencyScale: number;
    balance: string;
    equity: string;
    freeMargin: string;
    usdEquivalent: {
      balance: string;
      equity: string;
      freeMargin: string;
    };
  } | null;
  controls: {
    isPaused: boolean;
    killSwitchActive: boolean;
    demoAutoExecute?: boolean;
  };
  counts: {
    openPositions: number;
    pendingProposals: number;
  };
  risk: {
    daily: {
      startingEquity: string;
      currentEquity: string;
      lossLimit: string;
      drawdownPercent: number;
      isTripped: boolean;
    } | null;
    weekly: {
      startingEquity: string;
      currentEquity: string;
      lossLimit: string;
      drawdownPercent: number;
      isTripped: boolean;
      resetAt: string | null;
    } | null;
  };
}

export interface ClosedTradeForReport {
  id: string;
  symbol: string;
  side: "BUY" | "SELL";
  volume: string;
  profit: string;
  commission: string;
  swap: string;
  closedAt: string;
}

export interface AuditLogItem {
  id: string;
  actorId: string | null;
  actorEmail?: string | null;
  action: string;
  entityType: string;
  entityId: string | null;
  metadata: Record<string, unknown>;
  ip: string | null;
  createdAt: string;
}

export interface DashboardAndReportsRepository {
  getDashboardSummary(): Promise<DashboardSummaryData>;
  getClosedTradesForReport(input: {
    from?: Date;
    to?: Date;
  }): Promise<{
    accountCurrency: string;
    currencyScale: number;
    trades: ClosedTradeForReport[];
  }>;
  listAuditLogs(input: {
    action?: string;
    limit: number;
  }): Promise<AuditLogItem[]>;
}

export function computePerformanceReport(
  trades: ClosedTradeForReport[],
  accountCurrency: string,
  currencyScale: number,
) {
  const scale = currencyScale > 0 ? currencyScale : 1;
  let wins = 0;
  let losses = 0;
  let grossProfit = 0;
  let grossLoss = 0;
  let totalCommission = 0;
  let totalSwap = 0;
  let cumulative = 0;
  let peak = 0;
  let maxDrawdown = 0;

  const equityCurve: { closedAt: string; cumulativePnl: number; tradeNet: number }[] = [];

  for (const t of trades) {
    const p = Number(t.profit);
    const c = Number(t.commission);
    const s = Number(t.swap);
    const net = (Number.isFinite(p) ? p : 0) + (Number.isFinite(c) ? c : 0) + (Number.isFinite(s) ? s : 0);

    totalCommission += Number.isFinite(c) ? c : 0;
    totalSwap += Number.isFinite(s) ? s : 0;
    cumulative += net;

    if (cumulative > peak) peak = cumulative;
    const dd = peak - cumulative;
    if (dd > maxDrawdown) maxDrawdown = dd;

    if (net > 0) {
      wins++;
      grossProfit += net;
    } else {
      losses++;
      grossLoss += Math.abs(net);
    }

    equityCurve.push({
      closedAt: t.closedAt,
      cumulativePnl: Number(cumulative.toFixed(4)),
      tradeNet: Number(net.toFixed(4)),
    });
  }

  const totalTrades = trades.length;
  const winRatePercent =
    totalTrades > 0 ? Number(((wins / totalTrades) * 100).toFixed(2)) : 0;
  const profitFactor =
    grossLoss > 0
      ? Number((grossProfit / grossLoss).toFixed(3))
      : grossProfit > 0
        ? 999
        : null;
  const expectancy =
    totalTrades > 0 ? Number((cumulative / totalTrades).toFixed(4)) : 0;

  return {
    accountCurrency,
    currencyScale: scale,
    totalTrades,
    wins,
    losses,
    winRatePercent,
    netPnl: cumulative.toFixed(4),
    netPnlUsd: (cumulative / scale).toFixed(2),
    grossProfit: grossProfit.toFixed(4),
    grossLoss: grossLoss.toFixed(4),
    profitFactor,
    expectancy: expectancy.toFixed(4),
    maxDrawdown: maxDrawdown.toFixed(4),
    maxDrawdownUsd: (maxDrawdown / scale).toFixed(2),
    totalCommission: totalCommission.toFixed(4),
    totalSwap: totalSwap.toFixed(4),
    equityCurve,
  };
}

export function createDashboardRouter(opts: {
  repo: DashboardAndReportsRepository;
  requireAuth: RequestHandler;
}) {
  const router = Router();

  router.get(
    "/api/v1/dashboard/summary",
    opts.requireAuth,
    async (_req, res, next) => {
      try {
        const summary = await opts.repo.getDashboardSummary();
        res.status(200).json(summary);
      } catch (err) {
        next(err);
      }
    },
  );

  router.get(
    "/api/v1/reports/performance",
    opts.requireAuth,
    async (req, res, next) => {
      try {
        const q = ReportsQuerySchema.parse(req.query);
        const { trades, accountCurrency, currencyScale } =
          await opts.repo.getClosedTradesForReport({
            from: q.from ? new Date(q.from) : undefined,
            to: q.to ? new Date(q.to) : undefined,
          });
        const report = computePerformanceReport(
          trades,
          accountCurrency,
          currencyScale,
        );
        res.status(200).json({ report });
      } catch (err) {
        next(err);
      }
    },
  );

  router.get(
    "/api/v1/audit-logs",
    opts.requireAuth,
    async (req, res, next) => {
      try {
        const q = AuditLogsQuerySchema.parse(req.query);
        const logs = await opts.repo.listAuditLogs(q);
        res.status(200).json({ logs });
      } catch (err) {
        next(err);
      }
    },
  );

  return router;
}

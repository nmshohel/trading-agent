import { Router, type RequestHandler } from "express";
import { z } from "zod";
import crypto from "node:crypto";
import { REDIS_KEYS } from "@trading-agent/shared";

export const HeartbeatPayloadSchema = z.object({
  workerId: z.string().min(1).default("mt5-worker-1"),
  mt5Connected: z.boolean(),
  terminalTradeAllowed: z.boolean(),
  accountTradeAllowed: z.boolean(),
  accountLogin: z.string().min(1).optional(),
  accountServer: z.string().min(1).optional(),
  accountMode: z.enum(["DEMO", "LIVE"]).optional(),
  symbol: z.string().min(1).optional(),
  serverTimeOffsetSeconds: z.number().int().optional(),
  timestamp: z.string().datetime({ offset: true }),
});

export type HeartbeatPayload = z.infer<typeof HeartbeatPayloadSchema>;

export const SymbolSpecsSchema = z.object({
  symbol: z.string().min(1),
  volumeMin: z.string().min(1),
  volumeMax: z.string().min(1),
  volumeStep: z.string().min(1),
  tradeContractSize: z.string().min(1),
  tradeTickSize: z.string().min(1),
  tradeTickValue: z.string().min(1),
  point: z.string().min(1),
  digits: z.number().int().nonnegative(),
  tradeStopsLevel: z.number().int().nonnegative(),
  tradeFreezeLevel: z.number().int().nonnegative(),
  fillingMode: z.enum(["FOK", "IOC", "RETURN"]),
  fillingModeFlags: z.number().int().nonnegative(),
  expirationModeFlags: z.number().int().nonnegative(),
  tradeMode: z.number().int().nonnegative(),
});

export const AccountSyncPayloadSchema = z.object({
  broker: z.string().min(1).default("Exness"),
  platform: z.string().min(1).default("MT5"),
  login: z.string().min(1),
  server: z.string().min(1),
  accountCurrency: z.string().min(1),
  currencyScale: z.number().int().positive(),
  accountType: z.enum(["STANDARD", "CENT"]),
  marginMode: z.enum(["HEDGING", "NETTING", "EXCHANGE"]),
  magicNumber: z.number().int().positive(),
  mode: z.enum(["DEMO", "LIVE"]),
  balance: z.string().min(1),
  equity: z.string().min(1),
  freeMargin: z.string().min(1),
  symbolSpecs: SymbolSpecsSchema,
});

export type AccountSyncPayload = z.infer<typeof AccountSyncPayloadSchema>;

export const SystemEventPayloadSchema = z.object({
  service: z.string().min(1),
  severity: z.enum(["INFO", "WARN", "ERROR", "CRITICAL"]).default("INFO"),
  message: z.string().min(1),
  metadata: z.record(z.unknown()).default({}),
});

export type SystemEventPayload = z.infer<typeof SystemEventPayloadSchema>;

export const MarketSnapshotPayloadSchema = z.object({
  symbol: z.string().min(1),
  timeframe: z.enum(["M5", "M15", "H1"]),
  candleTime: z.string().datetime({ offset: true }),
  open: z.string().min(1),
  high: z.string().min(1),
  low: z.string().min(1),
  close: z.string().min(1),
  volume: z.string().min(1).default("0"),
  bid: z.string().min(1),
  ask: z.string().min(1),
  spread: z.string().min(1),
  indicators: z.record(z.unknown()).default({}),
});

export type MarketSnapshotPayload = z.infer<typeof MarketSnapshotPayloadSchema>;

export const RiskSnapshotsSyncSchema = z.object({
  brokerAccountId: z.string().min(1),
  date: z.string().regex(/^\d{4}-\d{2}-\d{2}$/),
  weekStart: z.string().regex(/^\d{4}-\d{2}-\d{2}$/),
  startingDailyEquity: z.string().min(1),
  startingWeeklyEquity: z.string().min(1),
  currentEquity: z.string().min(1),
  realizedDailyPnl: z.string().default("0"),
  realizedWeeklyPnl: z.string().default("0"),
  unrealizedPnl: z.string().default("0"),
  dailyLossLimit: z.string().min(1),
  weeklyLossLimit: z.string().min(1),
  dailyTripped: z.boolean().default(false),
  weeklyTripped: z.boolean().default(false),
});

export type RiskSnapshotsSyncPayload = z.infer<typeof RiskSnapshotsSyncSchema>;

export const CreateInternalProposalSchema = z.object({
  dedupKey: z.string().min(1),
  brokerAccountId: z.string().min(1),
  strategyId: z.string().min(1),
  symbol: z.string().min(1),
  timeframe: z.enum(["M5", "M15", "H1"]),
  direction: z.enum(["BUY", "SELL"]),
  entry: z.string().min(1),
  stopLoss: z.string().min(1),
  takeProfit: z.string().min(1),
  volume: z.string().min(1),
  riskAmount: z.string().min(1),
  riskPercent: z.string().min(1),
  rewardRisk: z.string().min(1),
  estimatedMargin: z.string().nullable().optional(),
  spreadAtProposal: z.string().min(1),
  rationale: z.string().min(1),
  aiStatus: z.enum(["COMPLETED", "VETOED", "UNAVAILABLE", "SKIPPED"]),
  aiModel: z.string().nullable().optional(),
  aiPromptVersion: z.string().nullable().optional(),
  aiLatencyMs: z.number().int().nonnegative().nullable().optional(),
  aiTokensUsed: z.number().int().nonnegative().nullable().optional(),
  aiRawResponse: z.record(z.unknown()).nullable().optional(),
  status: z
    .enum(["PENDING_APPROVAL", "AI_VETOED", "RISK_REJECTED"])
    .default("PENDING_APPROVAL"),
  rejectionReason: z.string().nullable().optional(),
  expiresAt: z.string().datetime({ offset: true }),
});

export type CreateInternalProposalPayload = z.infer<
  typeof CreateInternalProposalSchema
>;

export const OrderResultPayloadSchema = z.object({
  proposalId: z.string().nullable().optional(),
  positionId: z.string().nullable().optional(),
  brokerAccountId: z.string().min(1),
  actionType: z.enum(["OPEN", "CLOSE"]),
  idempotencyKey: z.string().min(1),
  symbol: z.string().min(1),
  side: z.enum(["BUY", "SELL"]),
  volume: z.string().min(1),
  requestedPrice: z.string().nullable().optional(),
  entryPrice: z.string().nullable().optional(),
  stopLoss: z.string().nullable().optional(),
  takeProfit: z.string().nullable().optional(),
  fillingMode: z.string().nullable().optional(),
  deviationPoints: z.number().int().nullable().optional(),
  retcode: z.number().int().nullable().optional(),
  brokerOrderId: z.string().nullable().optional(),
  brokerDealId: z.string().nullable().optional(),
  brokerPositionId: z.string().nullable().optional(),
  brokerComment: z.string().nullable().optional(),
  orderStatus: z.enum([
    "REQUESTED",
    "ACCEPTED",
    "PARTIALLY_FILLED",
    "FILLED",
    "REJECTED",
    "CANCELLED",
    "UNKNOWN",
  ]),
  proposalStatus: z
    .enum(["EXECUTED", "EXECUTION_FAILED", "CANCELLED"])
    .nullable()
    .optional(),
  positionStatus: z
    .enum(["OPEN", "CLOSED", "UNKNOWN"])
    .nullable()
    .optional(),
  closePrice: z.string().nullable().optional(),
  realizedProfit: z.string().nullable().optional(),
  commission: z.string().nullable().optional(),
  swap: z.string().nullable().optional(),
  failureReason: z.string().nullable().optional(),
  eventPayload: z.record(z.unknown()).default({}),
});

export type OrderResultPayload = z.infer<typeof OrderResultPayloadSchema>;

export const PositionSyncItemSchema = z.object({
  brokerPositionId: z.string().min(1),
  symbol: z.string().min(1),
  side: z.enum(["BUY", "SELL"]),
  volume: z.string().min(1),
  openPrice: z.string().min(1),
  currentPrice: z.string().min(1),
  closePrice: z.string().nullable().optional(),
  stopLoss: z.string().nullable().optional(),
  takeProfit: z.string().nullable().optional(),
  profit: z.string().default("0"),
  commission: z.string().default("0"),
  swap: z.string().default("0"),
  magicNumber: z.number().int().nullable().optional(),
  isBotManaged: z.boolean().default(true),
  status: z.enum(["OPEN", "CLOSED", "UNKNOWN"]).default("OPEN"),
  openedAt: z.string().datetime({ offset: true }),
  closedAt: z.string().datetime({ offset: true }).nullable().optional(),
});

export const PositionsSyncPayloadSchema = z.object({
  brokerAccountId: z.string().min(1),
  positions: z.array(PositionSyncItemSchema),
});

export type PositionsSyncPayload = z.infer<typeof PositionsSyncPayloadSchema>;

export interface InternalWorkerRepository {
  getRuntimeState(): Promise<Record<string, unknown>>;
  findBrokerAccount(
    login: string,
    server: string,
  ): Promise<{
    id: string;
    mode: "DEMO" | "LIVE";
    liveUnlockedAt: Date | null;
  } | null>;
  upsertBrokerAccount(
    payload: AccountSyncPayload,
  ): Promise<Record<string, unknown>>;
  upsertMarketSnapshot(
    payload: MarketSnapshotPayload,
  ): Promise<{ id: string; candleTime: string }>;
  syncRiskSnapshots?(
    payload: RiskSnapshotsSyncPayload,
  ): Promise<{ dailyId: string; weeklyId: string }>;
  createProposal?(
    payload: CreateInternalProposalPayload,
  ): Promise<
    | { ok: true; proposal: Record<string, unknown> }
    | { ok: false; code: number; error: string }
  >;
  expirePendingProposals?(
    now: Date,
  ): Promise<{ expiredIds: string[] }>;
  claimProposalAtomic?(
    proposalId: string,
    now: Date,
  ): Promise<
    | { ok: true; proposal: Record<string, unknown> }
    | { ok: false; code: number; error: string }
  >;
  getStuckReconciliation?(thresholdSeconds: number): Promise<{
    stuckProposals: Record<string, unknown>[];
    stuckPositions: Record<string, unknown>[];
  }>;
  recordOrderResult?(
    payload: OrderResultPayload,
  ): Promise<{ orderId: string; positionId: string | null }>;
  syncPositions?(
    payload: PositionsSyncPayload,
  ): Promise<{ syncedCount: number }>;
  createSystemEvent(
    payload: SystemEventPayload,
  ): Promise<{ id: string; createdAt: Date }>;
  saveHeartbeat(payload: HeartbeatPayload): Promise<void>;
  publishSseEvent(event: {
    id: string;
    type: string;
    timestamp: string;
    data: Record<string, unknown>;
  }): Promise<void>;
}

export function createInternalRouter(opts: {
  repo: InternalWorkerRepository;
  requireWorkerToken: RequestHandler;
}) {
  const router = Router();
  router.use("/api/internal/v1", opts.requireWorkerToken);

  router.get("/api/internal/v1/ping", (_req, res) => {
    res.status(200).json({ ok: true, timestamp: new Date().toISOString() });
  });

  router.get("/api/internal/v1/runtime-state", async (_req, res, next) => {
    try {
      const state = await opts.repo.getRuntimeState();
      res.status(200).json(state);
    } catch (err) {
      next(err);
    }
  });

  router.post("/api/internal/v1/heartbeat", async (req, res, next) => {
    try {
      const payload = HeartbeatPayloadSchema.parse(req.body);
      await opts.repo.saveHeartbeat(payload);
      await opts.repo.publishSseEvent({
        id: crypto.randomUUID(),
        type: "system.health",
        timestamp: new Date().toISOString(),
        data: {
          channel: REDIS_KEYS.SSE_PUBSUB_CHANNEL,
          workerId: payload.workerId,
          mt5Connected: payload.mt5Connected,
          terminalTradeAllowed: payload.terminalTradeAllowed,
          accountTradeAllowed: payload.accountTradeAllowed,
          accountMode: payload.accountMode ?? null,
          symbol: payload.symbol ?? null,
          lastHeartbeatAt: payload.timestamp,
        },
      });
      res.status(200).json({ ok: true, receivedAt: new Date().toISOString() });
    } catch (err) {
      next(err);
    }
  });

  router.post("/api/internal/v1/account/sync", async (req, res, next) => {
    try {
      const payload = AccountSyncPayloadSchema.parse(req.body);
      const existing = await opts.repo.findBrokerAccount(
        payload.login,
        payload.server,
      );

      // Fail-closed: worker cannot silently register or upgrade an unlocked LIVE account
      if (payload.mode === "LIVE" && (!existing || !existing.liveUnlockedAt)) {
        await opts.repo.createSystemEvent({
          service: "api",
          severity: "CRITICAL",
          message:
            "Blocked LIVE account sync because live mode is not explicitly unlocked in DB",
          metadata: {
            login: payload.login,
            server: payload.server,
            accountType: payload.accountType,
          },
        });
        res.status(403).json({
          error: "LIVE account mode is locked; explicit live unlock required",
        });
        return;
      }

      const account = await opts.repo.upsertBrokerAccount(payload);
      res.status(200).json({ account });
    } catch (err) {
      next(err);
    }
  });

  router.post("/api/internal/v1/market/snapshot", async (req, res, next) => {
    try {
      const payload = MarketSnapshotPayloadSchema.parse(req.body);
      const saved = await opts.repo.upsertMarketSnapshot(payload);
      await opts.repo.publishSseEvent({
        id: crypto.randomUUID(),
        type: "quote.updated",
        timestamp: new Date().toISOString(),
        data: {
          symbol: payload.symbol,
          timeframe: payload.timeframe,
          candleTime: payload.candleTime,
          bid: payload.bid,
          ask: payload.ask,
          spread: payload.spread,
          close: payload.close,
          candleClosed: true,
        },
      });
      res.status(200).json({ ok: true, snapshot: saved });
    } catch (err) {
      next(err);
    }
  });

  router.post("/api/internal/v1/risk/snapshots", async (req, res, next) => {
    try {
      const payload = RiskSnapshotsSyncSchema.parse(req.body);
      if (!opts.repo.syncRiskSnapshots) {
        res.status(501).json({ error: "Risk snapshot sync not configured" });
        return;
      }
      const synced = await opts.repo.syncRiskSnapshots(payload);
      if (payload.dailyTripped || payload.weeklyTripped) {
        await opts.repo.publishSseEvent({
          id: crypto.randomUUID(),
          type: "risk.alert",
          timestamp: new Date().toISOString(),
          data: {
            code: payload.weeklyTripped
              ? "WEEKLY_LOSS_LIMIT"
              : "DAILY_LOSS_LIMIT",
            message: payload.weeklyTripped
              ? "Weekly loss limit reached — manual review & reset required"
              : "Daily loss limit reached — new trades paused until next broker day",
            severity: "CRITICAL",
          },
        });
      }
      res.status(200).json({ ok: true, synced });
    } catch (err) {
      next(err);
    }
  });

  router.post("/api/internal/v1/proposals", async (req, res, next) => {
    try {
      const payload = CreateInternalProposalSchema.parse(req.body);
      if (!opts.repo.createProposal) {
        res.status(501).json({ error: "Proposal ingest not configured" });
        return;
      }
      const result = await opts.repo.createProposal(payload);
      if (!result.ok) {
        res.status(result.code).json({ error: result.error });
        return;
      }

      if (payload.status === "PENDING_APPROVAL") {
        const autoApproved = Boolean(result.proposal.autoApproved);
        await opts.repo.publishSseEvent({
          id: crypto.randomUUID(),
          type: "proposal.created",
          timestamp: new Date().toISOString(),
          data: {
            proposalId: result.proposal.id,
            symbol: payload.symbol,
            direction: payload.direction,
            entry: payload.entry,
            stopLoss: payload.stopLoss,
            takeProfit: payload.takeProfit,
            volume: payload.volume,
            expiresAt: payload.expiresAt,
            aiStatus: payload.aiStatus,
            autoApproved,
          },
        });
        if (autoApproved && result.proposal.status === "APPROVED") {
          await opts.repo.publishSseEvent({
            id: crypto.randomUUID(),
            type: "proposal.updated",
            timestamp: new Date().toISOString(),
            data: {
              proposalId: result.proposal.id,
              status: "APPROVED",
              autoApproved: true,
            },
          });
        }
      }

      res.status(201).json({ proposal: result.proposal });
    } catch (err) {
      next(err);
    }
  });

  router.post("/api/internal/v1/proposals/expire", async (_req, res, next) => {
    try {
      if (!opts.repo.expirePendingProposals) {
        res.status(501).json({ error: "Proposal expiry not configured" });
        return;
      }
      const { expiredIds } = await opts.repo.expirePendingProposals(new Date());
      for (const id of expiredIds) {
        await opts.repo.publishSseEvent({
          id: crypto.randomUUID(),
          type: "proposal.expired",
          timestamp: new Date().toISOString(),
          data: { proposalId: id, status: "EXPIRED" },
        });
      }
      res.status(200).json({ ok: true, expiredCount: expiredIds.length, expiredIds });
    } catch (err) {
      next(err);
    }
  });

  router.post("/api/internal/v1/proposals/:id/claim", async (req, res, next) => {
    try {
      const id = typeof req.params.id === "string" ? req.params.id : undefined;
      if (!id) {
        res.status(400).json({ error: "Proposal ID is required" });
        return;
      }
      if (!opts.repo.claimProposalAtomic) {
        res.status(501).json({ error: "Proposal claim not configured" });
        return;
      }
      const result = await opts.repo.claimProposalAtomic(id, new Date());
      if (!result.ok) {
        res.status(result.code).json({ error: result.error });
        return;
      }
      await opts.repo.publishSseEvent({
        id: crypto.randomUUID(),
        type: "proposal.updated",
        timestamp: new Date().toISOString(),
        data: { proposalId: id, status: "EXECUTING" },
      });
      res.status(200).json({ ok: true, proposal: result.proposal });
    } catch (err) {
      next(err);
    }
  });

  router.get("/api/internal/v1/reconciliation/stuck", async (req, res, next) => {
    try {
      if (!opts.repo.getStuckReconciliation) {
        res.status(501).json({ error: "Stuck reconciliation not configured" });
        return;
      }
      const rawThreshold = Number(req.query.thresholdSeconds ?? 45);
      const thresholdSeconds =
        Number.isFinite(rawThreshold) && rawThreshold >= 5
          ? Math.floor(rawThreshold)
          : 45;
      const result = await opts.repo.getStuckReconciliation(thresholdSeconds);
      res.status(200).json(result);
    } catch (err) {
      next(err);
    }
  });

  router.post("/api/internal/v1/orders/result", async (req, res, next) => {
    try {
      const payload = OrderResultPayloadSchema.parse(req.body);
      if (!opts.repo.recordOrderResult) {
        res.status(501).json({ error: "Order result persistence not configured" });
        return;
      }
      const saved = await opts.repo.recordOrderResult(payload);

      const ts = new Date().toISOString();
      await opts.repo.publishSseEvent({
        id: crypto.randomUUID(),
        type: "order.updated",
        timestamp: ts,
        data: {
          orderId: saved.orderId,
          actionType: payload.actionType,
          proposalId: payload.proposalId ?? null,
          positionId: saved.positionId,
          status: payload.orderStatus,
          retcode: payload.retcode ?? null,
          symbol: payload.symbol,
        },
      });

      if (payload.positionStatus === "CLOSED") {
        await opts.repo.publishSseEvent({
          id: crypto.randomUUID(),
          type: "position.closed",
          timestamp: ts,
          data: {
            positionId: saved.positionId,
            brokerPositionId: payload.brokerPositionId ?? null,
            closePrice: payload.closePrice ?? null,
            realizedProfit: payload.realizedProfit ?? null,
            symbol: payload.symbol,
          },
        });
      }

      res.status(200).json({ ok: true, ...saved });
    } catch (err) {
      next(err);
    }
  });

  router.post("/api/internal/v1/positions/sync", async (req, res, next) => {
    try {
      const payload = PositionsSyncPayloadSchema.parse(req.body);
      if (!opts.repo.syncPositions) {
        res.status(501).json({ error: "Position sync not configured" });
        return;
      }
      const result = await opts.repo.syncPositions(payload);
      await opts.repo.publishSseEvent({
        id: crypto.randomUUID(),
        type: "position.updated",
        timestamp: new Date().toISOString(),
        data: {
          brokerAccountId: payload.brokerAccountId,
          count: payload.positions.length,
        },
      });
      res.status(200).json({ ok: true, ...result });
    } catch (err) {
      next(err);
    }
  });

  router.post("/api/internal/v1/events", async (req, res, next) => {
    try {
      const payload = SystemEventPayloadSchema.parse(req.body);
      const created = await opts.repo.createSystemEvent(payload);
      res.status(201).json({
        id: created.id,
        createdAt: created.createdAt.toISOString(),
      });
    } catch (err) {
      next(err);
    }
  });

  return router;
}

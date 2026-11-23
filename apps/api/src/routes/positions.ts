import crypto from "node:crypto";
import { Router, type RequestHandler } from "express";
import rateLimit from "express-rate-limit";
import { z } from "zod";
import type { ClosePositionCommand } from "@trading-agent/shared";

export const ManualCloseSchema = z.object({
  idempotencyKey: z.string().trim().min(8).max(128),
  reason: z.string().trim().max(300).optional(),
});

export const ListPositionsQuerySchema = z.object({
  status: z.enum(["OPEN", "CLOSING", "CLOSED", "UNKNOWN"]).optional(),
  limit: z.coerce.number().int().min(1).max(100).default(50),
});

export const ListOrdersQuerySchema = z.object({
  limit: z.coerce.number().int().min(1).max(200).default(100),
});

export interface PositionRecord {
  id: string;
  brokerAccountId: string;
  accountLogin?: string;
  accountCurrency?: string;
  currencyScale?: number;
  brokerPositionId: string;
  symbol: string;
  side: "BUY" | "SELL";
  volume: string;
  openPrice: string;
  currentPrice: string;
  closePrice: string | null;
  stopLoss: string | null;
  takeProfit: string | null;
  profit: string;
  commission: string;
  swap: string;
  magicNumber: number | null;
  isBotManaged: boolean;
  status: "OPEN" | "CLOSING" | "CLOSED" | "UNKNOWN";
  closeRequestedAt: string | null;
  openedAt: string;
  closedAt: string | null;
}

export interface OrderRecord {
  id: string;
  proposalId: string | null;
  positionId: string | null;
  actionType: "OPEN" | "CLOSE";
  brokerOrderId: string | null;
  brokerDealId: string | null;
  symbol: string;
  side: "BUY" | "SELL";
  volume: string;
  requestedPrice: string | null;
  entryPrice: string | null;
  stopLoss: string | null;
  takeProfit: string | null;
  fillingMode: string | null;
  deviationPoints: number | null;
  retcode: number | null;
  brokerComment: string | null;
  status: string;
  createdAt: string;
}

export interface PositionsAndOrdersRepository {
  listPositions(input: {
    status?: "OPEN" | "CLOSING" | "CLOSED" | "UNKNOWN";
    limit: number;
  }): Promise<PositionRecord[]>;
  listOrders(limit: number): Promise<OrderRecord[]>;
  requestManualCloseAtomic(input: {
    positionId: string;
    idempotencyKey: string;
    now: Date;
  }): Promise<
    | { ok: true; position: PositionRecord }
    | { ok: false; code: number; error: string }
  >;
  enqueueCloseCommand(cmd: ClosePositionCommand): Promise<string>;
  writeAuditLog(input: {
    actorId?: string;
    action: string;
    entityType: string;
    entityId?: string;
    metadata: Record<string, unknown>;
    ip?: string;
  }): Promise<void>;
  publishSseEvent(event: {
    id: string;
    type: "position.updated" | "position.closed";
    timestamp: string;
    data: Record<string, unknown>;
  }): Promise<void>;
}

function withPositionUsd(p: PositionRecord) {
  const scale = p.currencyScale && p.currencyScale > 0 ? p.currencyScale : 1;
  const toUsd = (v: string) => {
    const n = Number(v);
    return Number.isFinite(n) ? (n / scale).toFixed(2) : "0.00";
  };
  return {
    ...p,
    usdEquivalent: {
      profit: toUsd(p.profit),
      commission: toUsd(p.commission),
      swap: toUsd(p.swap),
    },
  };
}

export function createPositionsRouter(opts: {
  repo: PositionsAndOrdersRepository;
  requireAuth: RequestHandler;
  requireOwner: RequestHandler;
  requireCsrf: RequestHandler;
}) {
  const router = Router();

  const closeLimiter = rateLimit({
    windowMs: 60 * 1000,
    max: 20,
    standardHeaders: true,
    legacyHeaders: false,
    message: { error: "Too many manual close requests" },
  });

  router.get(
    "/api/v1/positions",
    opts.requireAuth,
    async (req, res, next) => {
      try {
        const query = ListPositionsQuerySchema.parse(req.query);
        const rows = await opts.repo.listPositions(query);
        res.status(200).json({ positions: rows.map(withPositionUsd) });
      } catch (err) {
        next(err);
      }
    },
  );

  // Risk-reducing action: explicitly allowed even when paused, loss-limited, or Kill Switch is ON
  router.post(
    "/api/v1/positions/:id/close",
    closeLimiter,
    opts.requireAuth,
    opts.requireOwner,
    opts.requireCsrf,
    async (req, res, next) => {
      try {
        const id = typeof req.params.id === "string" ? req.params.id : undefined;
        if (!id) {
          res.status(400).json({ error: "Position ID is required" });
          return;
        }
        const parsed = ManualCloseSchema.parse(req.body);
        const now = new Date();

        const claimed = await opts.repo.requestManualCloseAtomic({
          positionId: id,
          idempotencyKey: parsed.idempotencyKey,
          now,
        });
        if (!claimed.ok) {
          res.status(claimed.code).json({ error: claimed.error });
          return;
        }

        const cmd: ClosePositionCommand = {
          type: "CLOSE_POSITION",
          commandId: crypto.randomUUID(),
          idempotencyKey: parsed.idempotencyKey,
          createdAt: now.toISOString(),
          payload: {
            positionId: claimed.position.id,
            brokerPositionId: claimed.position.brokerPositionId,
            accountLogin: claimed.position.accountLogin ?? "unknown",
            symbol: claimed.position.symbol,
            requestedByUserId: req.authUser!.id,
          },
        };
        const streamEntryId = await opts.repo.enqueueCloseCommand(cmd);

        await opts.repo.writeAuditLog({
          actorId: req.authUser?.id,
          action: "POSITION_MANUAL_CLOSE_REQUESTED",
          entityType: "Position",
          entityId: claimed.position.id,
          metadata: {
            brokerPositionId: claimed.position.brokerPositionId,
            symbol: claimed.position.symbol,
            volume: claimed.position.volume,
            idempotencyKey: parsed.idempotencyKey,
            commandId: cmd.commandId,
            streamEntryId,
            reason: parsed.reason ?? null,
          },
          ip: req.ip,
        });

        await opts.repo.publishSseEvent({
          id: crypto.randomUUID(),
          type: "position.updated",
          timestamp: now.toISOString(),
          data: {
            positionId: claimed.position.id,
            brokerPositionId: claimed.position.brokerPositionId,
            status: "CLOSING",
            commandId: cmd.commandId,
          },
        });

        res.status(202).json({
          position: withPositionUsd(claimed.position),
          commandId: cmd.commandId,
        });
      } catch (err) {
        next(err);
      }
    },
  );

  router.get("/api/v1/orders", opts.requireAuth, async (req, res, next) => {
    try {
      const query = ListOrdersQuerySchema.parse(req.query);
      const orders = await opts.repo.listOrders(query.limit);
      res.status(200).json({ orders });
    } catch (err) {
      next(err);
    }
  });

  return router;
}

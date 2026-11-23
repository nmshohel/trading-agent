import { Router, type RequestHandler } from "express";
import { z } from "zod";

export const WeeklyResetSchema = z.object({
  note: z
    .string()
    .trim()
    .min(10, "Provide a review note (at least 10 characters)")
    .max(500),
});

export interface WeeklyRiskRecord {
  id: string;
  brokerAccountId: string;
  weekStart: string;
  startingEquity: string;
  currentEquity: string;
  realizedPnl: string;
  unrealizedPnl: string;
  lossLimit: string;
  isTripped: boolean;
  trippedAt: string | null;
  resetByUserId: string | null;
  resetAt: string | null;
  resetNote: string | null;
}

export interface RiskResetRepository {
  getLatestWeeklySnapshot(): Promise<WeeklyRiskRecord | null>;
  resetWeeklySnapshot(input: {
    id: string;
    userId: string;
    note: string;
    resetAt: Date;
  }): Promise<WeeklyRiskRecord>;
  writeAuditLog(input: {
    actorId?: string;
    action: string;
    entityType: string;
    entityId?: string;
    metadata: Record<string, unknown>;
    ip?: string;
  }): Promise<void>;
  notifySettingsUpdated(reason: string): Promise<void>;
}

export function createRiskRouter(opts: {
  repo: RiskResetRepository;
  requireAuth: RequestHandler;
  requireOwner: RequestHandler;
  requireCsrf: RequestHandler;
}) {
  const router = Router();

  router.post(
    "/api/v1/risk/weekly/reset",
    opts.requireAuth,
    opts.requireOwner,
    opts.requireCsrf,
    async (req, res, next) => {
      try {
        const parsed = WeeklyResetSchema.parse(req.body);
        const latest = await opts.repo.getLatestWeeklySnapshot();
        if (!latest) {
          res.status(404).json({ error: "No weekly risk snapshot found" });
          return;
        }
        if (!latest.isTripped) {
          res.status(409).json({
            error: "Weekly loss limit is not currently tripped",
          });
          return;
        }

        const now = new Date();
        const updated = await opts.repo.resetWeeklySnapshot({
          id: latest.id,
          userId: req.authUser!.id,
          note: parsed.note,
          resetAt: now,
        });

        await opts.repo.writeAuditLog({
          actorId: req.authUser?.id,
          action: "WEEKLY_LOSS_LIMIT_RESET",
          entityType: "WeeklyRiskSnapshot",
          entityId: updated.id,
          metadata: {
            weekStart: updated.weekStart,
            startingEquity: updated.startingEquity,
            currentEquity: updated.currentEquity,
            note: parsed.note,
          },
          ip: req.ip,
        });
        await opts.repo.notifySettingsUpdated("weekly_risk_reset");

        res.status(200).json({ weeklySnapshot: updated });
      } catch (err) {
        next(err);
      }
    },
  );

  return router;
}

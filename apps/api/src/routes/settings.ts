import { Router, type RequestHandler } from "express";
import { z } from "zod";

const DecimalStringSchema = (min: number, max: number) =>
  z
    .union([z.number(), z.string()])
    .transform((v) => String(v).trim())
    .refine((s) => /^\d+(\.\d+)?$/.test(s), "Must be a non-negative decimal")
    .refine(
      (s) => {
        const n = Number(s);
        return Number.isFinite(n) && n >= min && n <= max;
      },
      `Must be between ${min} and ${max}`,
    );

export const UpdateTradingSettingsSchema = z
  .object({
    symbol: z.string().trim().min(3).max(20).optional(),
    riskPercent: DecimalStringSchema(0.05, 2.0).optional(),
    dailyLossLimit: DecimalStringSchema(0.25, 5.0).optional(),
    weeklyLossLimit: DecimalStringSchema(0.5, 10.0).optional(),
    maxPositions: z.number().int().min(1).max(1).optional(), // Strictly 1 in v1
    maxDailyTrades: z.number().int().min(1).max(20).optional(),
    maxPendingProposals: z.number().int().min(1).max(5).optional(),
    minRewardRisk: DecimalStringSchema(1.5, 5.0).optional(),
    maxSpread: DecimalStringSchema(0.05, 5.0).optional(),
    maxDeviationPoints: z.number().int().min(5).max(2000).optional(),
    commissionPerLot: DecimalStringSchema(0.0, 50.0).optional(),
    approvalExpirySeconds: z.number().int().min(30).max(300).optional(),
    postRejectionCooldownMinutes: z.number().int().min(1).max(240).optional(),
    rolloverBlackoutMinutes: z.number().int().min(15).max(180).optional(),
    aiFallbackMode: z.enum(["DETERMINISTIC_ONLY", "SKIP"]).optional(),
    demoAutoExecute: z.boolean().optional(),
    sessionWindows: z.record(z.unknown()).optional(),
  })
  .strict()
  .refine((obj) => Object.keys(obj).length > 0, {
    message: "At least one setting field must be provided",
  });

export type UpdateTradingSettingsInput = z.infer<
  typeof UpdateTradingSettingsSchema
>;

export interface TradingSettingsRecord {
  id: string;
  symbol: string;
  riskPercent: string;
  dailyLossLimit: string;
  weeklyLossLimit: string;
  maxPositions: number;
  maxDailyTrades?: number;
  maxPendingProposals: number;
  minRewardRisk: string;
  maxSpread: string;
  maxDeviationPoints: number;
  commissionPerLot: string;
  approvalExpirySeconds: number;
  postRejectionCooldownMinutes: number;
  rolloverBlackoutMinutes: number;
  sessionWindows: Record<string, unknown>;
  aiFallbackMode: "DETERMINISTIC_ONLY" | "SKIP";
  demoAutoExecute?: boolean;
  isPaused: boolean;
  killSwitchActive: boolean;
  cooldownUntil: string | null;
  updatedAt: string;
}

export interface TradingSettingsRepository {
  getSettings(): Promise<TradingSettingsRecord | null>;
  getActiveAccountMode?(): Promise<{
    mode: "DEMO" | "LIVE";
    liveUnlockedAt: string | null;
  } | null>;
  updateSettings(
    id: string,
    patch: UpdateTradingSettingsInput,
  ): Promise<TradingSettingsRecord>;
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

export function createSettingsRouter(opts: {
  repo: TradingSettingsRepository;
  requireAuth: RequestHandler;
  requireOwner: RequestHandler;
  requireCsrf: RequestHandler;
}) {
  const router = Router();

  router.get(
    "/api/v1/settings/trading",
    opts.requireAuth,
    async (_req, res, next) => {
      try {
        const settings = await opts.repo.getSettings();
        res.status(200).json({ settings });
      } catch (err) {
        next(err);
      }
    },
  );

  router.patch(
    "/api/v1/settings/trading",
    opts.requireAuth,
    opts.requireOwner,
    opts.requireCsrf,
    async (req, res, next) => {
      try {
        const patch = UpdateTradingSettingsSchema.parse(req.body);
        const existing = await opts.repo.getSettings();
        if (!existing) {
          res.status(404).json({ error: "Trading settings not initialized" });
          return;
        }

        if (patch.demoAutoExecute === true && opts.repo.getActiveAccountMode) {
          const acct = await opts.repo.getActiveAccountMode();
          if (acct && (acct.mode === "LIVE" || acct.liveUnlockedAt !== null)) {
            res.status(403).json({
              error:
                "Demo auto-execute cannot be enabled on a LIVE account; manual approval is strictly required for LIVE trading.",
            });
            return;
          }
        }

        const updated = await opts.repo.updateSettings(existing.id, patch);

        await opts.repo.writeAuditLog({
          actorId: req.authUser?.id,
          action: "TRADING_SETTINGS_UPDATED",
          entityType: "TradingSettings",
          entityId: existing.id,
          metadata: { patch },
          ip: req.ip,
        });
        await opts.repo.notifySettingsUpdated("trading_settings_updated");

        res.status(200).json({ settings: updated });
      } catch (err) {
        next(err);
      }
    },
  );

  return router;
}

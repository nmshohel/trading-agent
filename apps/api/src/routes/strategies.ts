import { Router, type RequestHandler } from "express";
import { z } from "zod";

export const UpdateStrategySchema = z
  .object({
    isActive: z.boolean().optional(),
    parameters: z
      .object({
        emaFast: z.number().int().min(5).max(50).optional(),
        emaMid: z.number().int().min(20).max(150).optional(),
        emaSlow: z.number().int().min(50).max(300).optional(),
        rsiPeriod: z.number().int().min(5).max(30).optional(),
        atrPeriod: z.number().int().min(5).max(30).optional(),
        slAtrBufferMultiplier: z.number().min(0.1).max(3.0).optional(),
        targetRewardRisk: z.number().min(1.5).max(5.0).optional(),
      })
      .strict()
      .optional(),
  })
  .refine(
    (data) => data.isActive !== undefined || data.parameters !== undefined,
    { message: "Provide isActive or parameters to update" },
  );

export interface StrategyRecord {
  id: string;
  name: string;
  version: string;
  timeframe: string;
  parameters: Record<string, unknown>;
  isActive: boolean;
  updatedAt: string;
}

export interface StrategyRepository {
  listStrategies(): Promise<StrategyRecord[]>;
  findStrategyById(id: string): Promise<StrategyRecord | null>;
  updateStrategy(
    id: string,
    input: { isActive?: boolean; parameters?: Record<string, unknown> },
  ): Promise<StrategyRecord>;
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

export function createStrategiesRouter(opts: {
  repo: StrategyRepository;
  requireAuth: RequestHandler;
  requireOwner: RequestHandler;
  requireCsrf: RequestHandler;
}) {
  const router = Router();

  router.get(
    "/api/v1/strategies",
    opts.requireAuth,
    async (_req, res, next) => {
      try {
        const strategies = await opts.repo.listStrategies();
        res.status(200).json({ strategies });
      } catch (err) {
        next(err);
      }
    },
  );

  router.patch(
    "/api/v1/strategies/:id",
    opts.requireAuth,
    opts.requireOwner,
    opts.requireCsrf,
    async (req, res, next) => {
      try {
        const id = typeof req.params.id === "string" ? req.params.id : undefined;
        if (!id) {
          res.status(400).json({ error: "Strategy ID is required" });
          return;
        }
        const parsed = UpdateStrategySchema.parse(req.body);
        const existing = await opts.repo.findStrategyById(id);
        if (!existing) {
          res.status(404).json({ error: "Strategy not found" });
          return;
        }

        const mergedParams = parsed.parameters
          ? { ...existing.parameters, ...parsed.parameters }
          : undefined;

        const updated = await opts.repo.updateStrategy(id, {
          isActive: parsed.isActive,
          parameters: mergedParams,
        });

        await opts.repo.writeAuditLog({
          actorId: req.authUser?.id,
          action: "STRATEGY_UPDATED",
          entityType: "Strategy",
          entityId: id,
          metadata: {
            previous: {
              isActive: existing.isActive,
              parameters: existing.parameters,
            },
            updated: {
              isActive: updated.isActive,
              parameters: updated.parameters,
            },
          },
          ip: req.ip,
        });
        await opts.repo.notifySettingsUpdated("strategy_updated");

        res.status(200).json({ strategy: updated });
      } catch (err) {
        next(err);
      }
    },
  );

  return router;
}

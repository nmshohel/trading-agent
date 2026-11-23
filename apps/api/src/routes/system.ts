import crypto from "node:crypto";
import { Router, type RequestHandler } from "express";
import { z } from "zod";

const KillSwitchBodySchema = z
  .object({
    reason: z.string().trim().max(300).optional(),
  })
  .default({});

const ResumeBodySchema = z
  .object({
    clearKillSwitch: z.boolean().default(false),
    note: z.string().trim().max(300).optional(),
  })
  .default({});

export interface SystemControlRepository {
  activateKillSwitch(reason?: string): Promise<{
    cancelledProposalsCount: number;
  }>;
  setPaused(isPaused: boolean): Promise<void>;
  resumeSystem(clearKillSwitch: boolean): Promise<{
    ok: boolean;
    error?: string;
  }>;
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
    type: "system.health" | "risk.alert" | "settings.updated";
    timestamp: string;
    data: Record<string, unknown>;
  }): Promise<void>;
}

export function createSystemControlRouter(opts: {
  repo: SystemControlRepository;
  requireAuth: RequestHandler;
  requireOwner: RequestHandler;
  requireCsrf: RequestHandler;
}) {
  const router = Router();

  router.post(
    "/api/v1/system/kill-switch",
    opts.requireAuth,
    opts.requireOwner,
    opts.requireCsrf,
    async (req, res, next) => {
      try {
        const parsed = KillSwitchBodySchema.parse(req.body ?? {});
        const { cancelledProposalsCount } = await opts.repo.activateKillSwitch(
          parsed.reason,
        );

        await opts.repo.writeAuditLog({
          actorId: req.authUser?.id,
          action: "KILL_SWITCH_ACTIVATED",
          entityType: "System",
          metadata: {
            reason: parsed.reason ?? "Owner emergency activation",
            cancelledProposalsCount,
          },
          ip: req.ip,
        });

        const ts = new Date().toISOString();
        await Promise.all([
          opts.repo.publishSseEvent({
            id: crypto.randomUUID(),
            type: "system.health",
            timestamp: ts,
            data: { killSwitchActive: true, isPaused: true },
          }),
          opts.repo.publishSseEvent({
            id: crypto.randomUUID(),
            type: "risk.alert",
            timestamp: ts,
            data: {
              code: "KILL_SWITCH_ACTIVATED",
              severity: "CRITICAL",
              message: `Kill switch activated. ${cancelledProposalsCount} pending/approved proposals cancelled. Existing open positions retain broker SL/TP.`,
            },
          }),
        ]);

        res.status(200).json({
          ok: true,
          killSwitchActive: true,
          cancelledProposalsCount,
        });
      } catch (err) {
        next(err);
      }
    },
  );

  router.post(
    "/api/v1/system/pause",
    opts.requireAuth,
    opts.requireOwner,
    opts.requireCsrf,
    async (req, res, next) => {
      try {
        await opts.repo.setPaused(true);
        await opts.repo.writeAuditLog({
          actorId: req.authUser?.id,
          action: "SYSTEM_PAUSED",
          entityType: "System",
          metadata: {},
          ip: req.ip,
        });

        const ts = new Date().toISOString();
        await opts.repo.publishSseEvent({
          id: crypto.randomUUID(),
          type: "system.health",
          timestamp: ts,
          data: { isPaused: true },
        });
        await opts.repo.publishSseEvent({
          id: crypto.randomUUID(),
          type: "settings.updated",
          timestamp: ts,
          data: { reason: "system_paused" },
        });

        res.status(200).json({ ok: true, isPaused: true });
      } catch (err) {
        next(err);
      }
    },
  );

  router.post(
    "/api/v1/system/resume",
    opts.requireAuth,
    opts.requireOwner,
    opts.requireCsrf,
    async (req, res, next) => {
      try {
        const parsed = ResumeBodySchema.parse(req.body ?? {});
        const result = await opts.repo.resumeSystem(parsed.clearKillSwitch);
        if (!result.ok) {
          res.status(409).json({
            error: result.error ?? "Cannot resume until health/risk gates pass",
          });
          return;
        }

        await opts.repo.writeAuditLog({
          actorId: req.authUser?.id,
          action: "SYSTEM_RESUMED",
          entityType: "System",
          metadata: {
            clearKillSwitch: parsed.clearKillSwitch,
            note: parsed.note ?? null,
          },
          ip: req.ip,
        });

        const ts = new Date().toISOString();
        await opts.repo.publishSseEvent({
          id: crypto.randomUUID(),
          type: "system.health",
          timestamp: ts,
          data: {
            isPaused: false,
            ...(parsed.clearKillSwitch ? { killSwitchActive: false } : {}),
          },
        });
        await opts.repo.publishSseEvent({
          id: crypto.randomUUID(),
          type: "settings.updated",
          timestamp: ts,
          data: { reason: "system_resumed" },
        });

        res.status(200).json({
          ok: true,
          isPaused: false,
          killSwitchCleared: parsed.clearKillSwitch,
        });
      } catch (err) {
        next(err);
      }
    },
  );

  return router;
}

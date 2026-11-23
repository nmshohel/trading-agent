import { Router, type RequestHandler } from "express";
import { z } from "zod";

export const CreateBlackoutSchema = z
  .object({
    title: z.string().trim().min(2).max(120),
    reason: z.string().trim().max(500).optional(),
    startsAt: z.string().datetime(),
    endsAt: z.string().datetime(),
    isActive: z.boolean().default(true),
  })
  .refine((d) => Date.parse(d.endsAt) > Date.parse(d.startsAt), {
    message: "endsAt must be strictly after startsAt",
    path: ["endsAt"],
  });

export interface BlackoutRecord {
  id: string;
  title: string;
  reason: string | null;
  startsAt: string;
  endsAt: string;
  isActive: boolean;
  createdById: string | null;
  createdAt: string;
}

export interface BlackoutRepository {
  listBlackouts(): Promise<BlackoutRecord[]>;
  createBlackout(input: {
    title: string;
    reason?: string;
    startsAt: Date;
    endsAt: Date;
    isActive: boolean;
    createdById?: string;
  }): Promise<BlackoutRecord>;
  deleteBlackout(id: string): Promise<boolean>;
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

export function createBlackoutsRouter(opts: {
  repo: BlackoutRepository;
  requireAuth: RequestHandler;
  requireOwner: RequestHandler;
  requireCsrf: RequestHandler;
}) {
  const router = Router();

  router.get(
    "/api/v1/blackouts",
    opts.requireAuth,
    async (_req, res, next) => {
      try {
        const blackouts = await opts.repo.listBlackouts();
        res.status(200).json({ blackouts });
      } catch (err) {
        next(err);
      }
    },
  );

  router.post(
    "/api/v1/blackouts",
    opts.requireAuth,
    opts.requireOwner,
    opts.requireCsrf,
    async (req, res, next) => {
      try {
        const parsed = CreateBlackoutSchema.parse(req.body);
        const blackout = await opts.repo.createBlackout({
          title: parsed.title,
          reason: parsed.reason,
          startsAt: new Date(parsed.startsAt),
          endsAt: new Date(parsed.endsAt),
          isActive: parsed.isActive,
          createdById: req.authUser?.id,
        });

        await opts.repo.writeAuditLog({
          actorId: req.authUser?.id,
          action: "BLACKOUT_CREATED",
          entityType: "BlackoutWindow",
          entityId: blackout.id,
          metadata: {
            title: blackout.title,
            startsAt: blackout.startsAt,
            endsAt: blackout.endsAt,
          },
          ip: req.ip,
        });
        await opts.repo.notifySettingsUpdated("blackout_created");

        res.status(201).json({ blackout });
      } catch (err) {
        next(err);
      }
    },
  );

  router.delete(
    "/api/v1/blackouts/:id",
    opts.requireAuth,
    opts.requireOwner,
    opts.requireCsrf,
    async (req, res, next) => {
      try {
        const id = typeof req.params.id === "string" ? req.params.id : undefined;
        if (!id) {
          res.status(400).json({ error: "Blackout ID is required" });
          return;
        }
        const deleted = await opts.repo.deleteBlackout(id);
        if (!deleted) {
          res.status(404).json({ error: "Blackout window not found" });
          return;
        }

        await opts.repo.writeAuditLog({
          actorId: req.authUser?.id,
          action: "BLACKOUT_DELETED",
          entityType: "BlackoutWindow",
          entityId: id,
          metadata: {},
          ip: req.ip,
        });
        await opts.repo.notifySettingsUpdated("blackout_deleted");

        res.status(200).json({ ok: true });
      } catch (err) {
        next(err);
      }
    },
  );

  return router;
}

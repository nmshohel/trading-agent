import crypto from "node:crypto";
import { Router, type RequestHandler } from "express";
import rateLimit from "express-rate-limit";
import { z } from "zod";
import { verifyPassword } from "../lib/crypto.js";

export const LIVE_UNLOCK_PHRASE = "UNLOCK LIVE TRADING ON EXNESS MT5";

export const UnlockLiveSchema = z.object({
  password: z.string().min(8).max(128),
  confirmationText: z.literal(LIVE_UNLOCK_PHRASE),
  demoSignoffConfirmed: z.literal(true),
  acceptCapitalLossRisk: z.literal(true),
});

export interface BrokerAccountSummary {
  id: string;
  broker: string;
  platform: string;
  login: string;
  server: string;
  accountCurrency: string;
  currencyScale: number;
  accountType: "STANDARD" | "CENT";
  marginMode: string;
  magicNumber: number;
  mode: "DEMO" | "LIVE";
  balance: string;
  equity: string;
  freeMargin: string;
  symbolSpecs: unknown;
  liveUnlockedAt?: string | null;
  liveUnlockedBy?: string | null;
  isEnabled: boolean;
  lastSyncedAt: string | null;
}

export interface AccountQueryRepository {
  getActiveBrokerAccount(): Promise<BrokerAccountSummary | null>;
  getUserPasswordHash?(userId: string): Promise<string | null>;
  unlockLiveMode?(input: {
    accountId: string;
    userId: string;
    unlockedAt: Date;
  }): Promise<BrokerAccountSummary>;
  lockDemoMode?(accountId: string): Promise<BrokerAccountSummary>;
  writeAuditLog?(input: {
    actorId?: string;
    action: string;
    entityType: string;
    entityId?: string;
    metadata: Record<string, unknown>;
    ip?: string;
  }): Promise<void>;
  publishSseEvent?(event: {
    id: string;
    type: "system.health" | "settings.updated";
    timestamp: string;
    data: Record<string, unknown>;
  }): Promise<void>;
}

function toUsdEquivalent(nativeAmount: string, currencyScale: number): string {
  const num = Number(nativeAmount);
  if (!Number.isFinite(num) || currencyScale <= 0) return "0.00";
  return (num / currencyScale).toFixed(2);
}

function serializeWithUsd(account: BrokerAccountSummary) {
  return {
    ...account,
    usdEquivalent: {
      balance: toUsdEquivalent(account.balance, account.currencyScale),
      equity: toUsdEquivalent(account.equity, account.currencyScale),
      freeMargin: toUsdEquivalent(account.freeMargin, account.currencyScale),
    },
  };
}

export function createAccountRouter(opts: {
  repo: AccountQueryRepository;
  requireAuth: RequestHandler;
  requireOwner?: RequestHandler;
  requireCsrf?: RequestHandler;
}) {
  const router = Router();

  const liveLockLimiter = rateLimit({
    windowMs: 15 * 60 * 1000,
    max: 10,
    standardHeaders: true,
    legacyHeaders: false,
    message: { error: "Too many live-mode unlock attempts" },
  });

  router.get("/api/v1/account", opts.requireAuth, async (_req, res, next) => {
    try {
      const account = await opts.repo.getActiveBrokerAccount();
      if (!account) {
        res.status(200).json({ account: null });
        return;
      }
      res.status(200).json({ account: serializeWithUsd(account) });
    } catch (err) {
      next(err);
    }
  });

  const ownerGuards: RequestHandler[] = [opts.requireAuth];
  if (opts.requireOwner) ownerGuards.push(opts.requireOwner);
  if (opts.requireCsrf) ownerGuards.push(opts.requireCsrf);

  router.post(
    "/api/v1/account/unlock-live",
    liveLockLimiter,
    ...ownerGuards,
    async (req, res, next) => {
      try {
        const parsed = UnlockLiveSchema.parse(req.body);
        if (!opts.repo.getUserPasswordHash || !opts.repo.unlockLiveMode) {
          res.status(501).json({ error: "Live unlock not configured" });
          return;
        }

        const userId = req.authUser!.id;
        const hash = await opts.repo.getUserPasswordHash(userId);
        if (!hash || !(await verifyPassword(parsed.password, hash))) {
          res
            .status(401)
            .json({ error: "Re-authentication failed: invalid password" });
          return;
        }

        const active = await opts.repo.getActiveBrokerAccount();
        if (!active) {
          res.status(404).json({ error: "No active broker account found" });
          return;
        }

        const now = new Date();
        const updated = await opts.repo.unlockLiveMode({
          accountId: active.id,
          userId,
          unlockedAt: now,
        });

        await opts.repo.writeAuditLog?.({
          actorId: userId,
          action: "LIVE_MODE_UNLOCKED",
          entityType: "BrokerAccount",
          entityId: active.id,
          metadata: {
            login: active.login,
            server: active.server,
            accountType: active.accountType,
            unlockedAt: now.toISOString(),
          },
          ip: req.ip,
        });

        await opts.repo.publishSseEvent?.({
          id: crypto.randomUUID(),
          type: "system.health",
          timestamp: now.toISOString(),
          data: { accountMode: "LIVE" },
        });

        res.status(200).json({ account: serializeWithUsd(updated) });
      } catch (err) {
        next(err);
      }
    },
  );

  router.post(
    "/api/v1/account/lock-demo",
    ...ownerGuards,
    async (req, res, next) => {
      try {
        if (!opts.repo.lockDemoMode) {
          res.status(501).json({ error: "Demo lock not configured" });
          return;
        }
        const active = await opts.repo.getActiveBrokerAccount();
        if (!active) {
          res.status(404).json({ error: "No active broker account found" });
          return;
        }

        const updated = await opts.repo.lockDemoMode(active.id);
        await opts.repo.writeAuditLog?.({
          actorId: req.authUser?.id,
          action: "LIVE_MODE_LOCKED_TO_DEMO",
          entityType: "BrokerAccount",
          entityId: active.id,
          metadata: { login: active.login, server: active.server },
          ip: req.ip,
        });
        await opts.repo.publishSseEvent?.({
          id: crypto.randomUUID(),
          type: "system.health",
          timestamp: new Date().toISOString(),
          data: { accountMode: "DEMO" },
        });

        res.status(200).json({ account: serializeWithUsd(updated) });
      } catch (err) {
        next(err);
      }
    },
  );

  return router;
}

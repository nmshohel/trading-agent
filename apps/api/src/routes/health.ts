import { Router, type RequestHandler } from "express";
import type { HeartbeatPayload } from "./internal.js";

export interface HealthChecker {
  checkDatabase(): Promise<boolean>;
  checkRedis(): Promise<boolean>;
  getWorkerHeartbeat(): Promise<HeartbeatPayload | null>;
}

const HEARTBEAT_STALE_SECONDS = 30;

export function createHealthRouter(opts: {
  checker: HealthChecker;
  requireAuth: RequestHandler;
}) {
  const router = Router();

  // Unauthenticated liveness probe — exposes no sensitive details
  router.get("/health", (_req, res) => {
    res.status(200).json({
      status: "ok",
      timestamp: new Date().toISOString(),
    });
  });

  // Authenticated detailed system health
  router.get("/api/v1/system/health", opts.requireAuth, async (_req, res) => {
    const [dbOk, redisOk, heartbeat] = await Promise.all([
      opts.checker.checkDatabase().catch(() => false),
      opts.checker.checkRedis().catch(() => false),
      opts.checker.getWorkerHeartbeat().catch(() => null),
    ]);

    let workerStatus: "up" | "stale" | "not_connected" = "not_connected";
    let mt5Status: "connected" | "disconnected" | "not_connected" =
      "not_connected";
    let heartbeatAgeSeconds: number | null = null;

    if (heartbeat) {
      const hbMs = Date.parse(heartbeat.timestamp);
      if (Number.isFinite(hbMs)) {
        heartbeatAgeSeconds = Math.max(
          0,
          Math.floor((Date.now() - hbMs) / 1000),
        );
        workerStatus =
          heartbeatAgeSeconds <= HEARTBEAT_STALE_SECONDS ? "up" : "stale";
      }
      mt5Status = heartbeat.mt5Connected ? "connected" : "disconnected";
    }

    const coreHealthy = dbOk && redisOk;
    res.status(coreHealthy ? 200 : 503).json({
      status: coreHealthy ? "ok" : "degraded",
      timestamp: new Date().toISOString(),
      services: {
        database: dbOk ? "up" : "down",
        redis: redisOk ? "up" : "down",
        worker: workerStatus,
        mt5: mt5Status,
      },
      workerDetails: heartbeat
        ? {
            workerId: heartbeat.workerId,
            heartbeatAgeSeconds,
            lastHeartbeatAt: heartbeat.timestamp,
            terminalTradeAllowed: heartbeat.terminalTradeAllowed,
            accountTradeAllowed: heartbeat.accountTradeAllowed,
            accountMode: heartbeat.accountMode ?? null,
            symbol: heartbeat.symbol ?? null,
            serverTimeOffsetSeconds: heartbeat.serverTimeOffsetSeconds ?? null,
          }
        : null,
    });
  });

  return router;
}

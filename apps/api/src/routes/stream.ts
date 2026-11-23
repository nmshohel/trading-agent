import crypto from "node:crypto";
import { Router, type RequestHandler } from "express";
import type { SseHub } from "../lib/sseHub.js";

export function createStreamRouter(opts: {
  sseHub: SseHub;
  requireAuth: RequestHandler;
}) {
  const router = Router();

  router.get("/api/v1/stream", opts.requireAuth, (req, res) => {
    const userId = req.authUser?.id;
    if (!userId) {
      res.status(401).json({ error: "Authentication required" });
      return;
    }

    if (!opts.sseHub.canAccept(userId)) {
      res.status(429).json({
        error: "Too many concurrent SSE connections for this user",
      });
      return;
    }

    res.status(200);
    res.setHeader("Content-Type", "text/event-stream");
    res.setHeader("Cache-Control", "no-cache, no-transform");
    res.setHeader("Connection", "keep-alive");
    res.setHeader("X-Accel-Buffering", "no");
    res.flushHeaders?.();

    const lastEventId =
      req.header("last-event-id") ??
      (typeof req.query.lastEventId === "string"
        ? req.query.lastEventId
        : undefined);

    const clientId = crypto.randomUUID();
    const cleanup = opts.sseHub.register(clientId, userId, res, lastEventId);

    // On Vercel serverless, close gracefully before function timeout so clients reconnect cleanly
    let serverlessTimer: NodeJS.Timeout | undefined;
    if (process.env.VERCEL === "1") {
      serverlessTimer = setTimeout(() => {
        cleanup();
        if (!res.writableEnded) res.end();
      }, 15_000);
    }

    const done = () => {
      if (serverlessTimer) clearTimeout(serverlessTimer);
      cleanup();
    };
    req.on("close", done);
    req.on("error", done);
  });

  return router;
}

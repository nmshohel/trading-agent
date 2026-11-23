import { describe, it, expect, beforeEach } from "vitest";
import http from "node:http";
import type { AddressInfo } from "node:net";
import request from "supertest";
import { createApp } from "../src/app.js";
import { loadEnv } from "../src/config/env.js";
import { sha256 } from "../src/lib/crypto.js";
import { SseHub } from "../src/lib/sseHub.js";
import type { AuthStore, SessionRecord } from "../src/middleware/auth.js";
import type {
  InternalWorkerRepository,
  MarketSnapshotPayload,
} from "../src/routes/internal.js";
import type {
  CandleRecord,
  LatestQuoteRecord,
  MarketQueryRepository,
} from "../src/routes/market.js";

describe("Phase 2 API: market snapshots, candles & authenticated SSE stream", () => {
  const testConfig = loadEnv({
    NODE_ENV: "test",
    API_PORT: "5000",
    CORS_ORIGIN: "http://localhost:3000",
    DATABASE_URL: "postgresql://test:test@127.0.0.1:5432/test",
    REDIS_URL: "redis://127.0.0.1:6379",
    APP_SESSION_SECRET: "test_session_secret_at_least_32_chars",
    SESSION_COOKIE_NAME: "ta_session",
    SESSION_TTL_HOURS: "24",
    WORKER_AUTH_TOKEN: "test_worker_token_at_least_32_chars_long",
  });

  const sessionToken = "raw_owner_session_token_for_phase2";
  let candlesStore: CandleRecord[] = [];
  let latestQuote: LatestQuoteRecord | null = null;
  let sseHub: SseHub;

  beforeEach(() => {
    candlesStore = [];
    latestQuote = null;
    sseHub = new SseHub({
      maxConnectionsPerUser: 2,
      heartbeatIntervalMs: 60_000,
    });
  });

  function buildApp() {
    const session: SessionRecord = {
      id: "sess_p2",
      userId: "usr_owner_1",
      csrfToken: "csrf_p2",
      expiresAt: new Date(Date.now() + 3600_000),
      user: {
        id: "usr_owner_1",
        email: "owner@local.dev",
        role: "OWNER",
        isActive: true,
      },
    };

    const authStore: AuthStore = {
      async findSessionByTokenHash(hash) {
        return hash === sha256(sessionToken) ? session : null;
      },
    };

    const internalRepo: InternalWorkerRepository = {
      async getRuntimeState() {
        return {};
      },
      async findBrokerAccount() {
        return null;
      },
      async upsertBrokerAccount() {
        return {};
      },
      async upsertMarketSnapshot(payload: MarketSnapshotPayload) {
        candlesStore.push({
          symbol: payload.symbol,
          timeframe: payload.timeframe,
          candleTime: payload.candleTime,
          open: payload.open,
          high: payload.high,
          low: payload.low,
          close: payload.close,
          volume: payload.volume,
          bid: payload.bid,
          ask: payload.ask,
          spread: payload.spread,
          indicators: payload.indicators,
        });
        latestQuote = {
          symbol: payload.symbol,
          bid: payload.bid,
          ask: payload.ask,
          spread: payload.spread,
          quoteTime: new Date().toISOString(),
          indicators: payload.indicators,
        };
        return { id: `snap_${candlesStore.length}`, candleTime: payload.candleTime };
      },
      async createSystemEvent() {
        return { id: "evt_1", createdAt: new Date() };
      },
      async saveHeartbeat() {},
      async publishSseEvent(event) {
        sseHub.broadcast({
          id: event.id,
          type: event.type as "quote.updated",
          timestamp: event.timestamp,
          data: event.data,
        });
      },
    };

    const marketRepo: MarketQueryRepository = {
      async getLatestQuote() {
        return latestQuote;
      },
      async getCandles({ timeframe, limit }) {
        return candlesStore
          .filter((c) => c.timeframe === timeframe)
          .slice(-limit);
      },
    };

    return createApp({
      config: testConfig,
      authStore,
      internalRepo,
      marketRepo,
      sseHub,
      healthChecker: {
        async checkDatabase() {
          return true;
        },
        async checkRedis() {
          return true;
        },
        async getWorkerHeartbeat() {
          return null;
        },
      },
    });
  }

  it("ingests closed candle snapshots and serves /api/v1/market/xauusd and /candles", async () => {
    const app = buildApp();

    const pushRes = await request(app)
      .post("/api/internal/v1/market/snapshot")
      .set("Authorization", `Bearer ${testConfig.WORKER_AUTH_TOKEN}`)
      .send({
        symbol: "XAUUSDc",
        timeframe: "M15",
        candleTime: "2026-11-23T12:00:00.000Z",
        open: "2650.10",
        high: "2655.40",
        low: "2649.80",
        close: "2654.20",
        volume: "1420",
        bid: "2654.15",
        ask: "2654.35",
        spread: "0.20",
        indicators: { ema20: 2651.5 },
      });

    expect(pushRes.status).toBe(200);

    const quoteRes = await request(app)
      .get("/api/v1/market/xauusd")
      .set("Cookie", [`ta_session=${sessionToken}`]);

    expect(quoteRes.status).toBe(200);
    expect(quoteRes.body.isStale).toBe(false);
    expect(quoteRes.body.quote.bid).toBe("2654.15");

    const candlesRes = await request(app)
      .get("/api/v1/market/xauusd/candles?timeframe=M15&limit=50")
      .set("Cookie", [`ta_session=${sessionToken}`]);

    expect(candlesRes.status).toBe(200);
    expect(candlesRes.body.count).toBe(1);
    expect(candlesRes.body.candles[0].open).toBe(2650.1);

    const badTf = await request(app)
      .get("/api/v1/market/xauusd/candles?timeframe=D1")
      .set("Cookie", [`ta_session=${sessionToken}`]);
    expect(badTf.status).toBe(400);
  });

  it("rejects unauthenticated access to /api/v1/stream", async () => {
    const app = buildApp();
    const res = await request(app).get("/api/v1/stream");
    expect(res.status).toBe(401);
  });

  it("streams SSE headers, broadcasts live events, replays Last-Event-ID, and caps connections", async () => {
    const app = buildApp();
    const server = http.createServer(app);
    await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
    const { port } = server.address() as AddressInfo;

    try {
      // Seed replay buffer with 2 events
      sseHub.broadcast({
        id: "evt-1",
        type: "quote.updated",
        timestamp: new Date().toISOString(),
        data: { symbol: "XAUUSDc", bid: "2650.00", ask: "2650.20", spread: "0.20" },
      });
      sseHub.broadcast({
        id: "evt-2",
        type: "quote.updated",
        timestamp: new Date().toISOString(),
        data: { symbol: "XAUUSDc", bid: "2651.00", ask: "2651.20", spread: "0.20" },
      });

      const received = await new Promise<{
        headers: http.IncomingHttpHeaders;
        chunks: string;
      }>((resolve, reject) => {
        const req = http.request(
          {
            hostname: "127.0.0.1",
            port,
            path: "/api/v1/stream",
            method: "GET",
            headers: {
              Cookie: `ta_session=${sessionToken}`,
              "Last-Event-ID": "evt-1",
            },
          },
          (res) => {
            let chunks = "";
            res.on("data", (d: Buffer) => {
              chunks += d.toString("utf8");
              if (chunks.includes("evt-3")) {
                req.destroy();
                resolve({ headers: res.headers, chunks });
              }
            });
            // Broadcast a live event after connection opens
            setTimeout(() => {
              sseHub.broadcast({
                id: "evt-3",
                type: "quote.updated",
                timestamp: new Date().toISOString(),
                data: {
                  symbol: "XAUUSDc",
                  bid: "2652.00",
                  ask: "2652.20",
                  spread: "0.20",
                },
              });
            }, 30);
          },
        );
        req.on("error", reject);
        req.end();
      });

      expect(received.headers["content-type"]).toContain("text/event-stream");
      expect(received.headers["x-accel-buffering"]).toBe("no");
      expect(received.headers["cache-control"]).toContain("no-cache");
      expect(received.chunks).toContain("id: evt-2"); // replayed after evt-1
      expect(received.chunks).not.toContain("id: evt-1");
      expect(received.chunks).toContain("id: evt-3"); // live broadcast
      expect(received.chunks).toContain("event: quote.updated");
    } finally {
      sseHub.closeAll();
      await new Promise<void>((resolve) => server.close(() => resolve()));
    }
  });
});

import { describe, it, expect } from "vitest";
import request from "supertest";
import { createApp } from "../src/app.js";
import { loadEnv } from "../src/config/env.js";
import { sha256 } from "../src/lib/crypto.js";
import type { AuthStore, SessionRecord } from "../src/middleware/auth.js";
import type { DashboardAndReportsRepository } from "../src/routes/dashboard.js";

describe("Phase 8 API: dashboard summary, performance report & audit logs", () => {
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

  const sessionToken = "raw_owner_session_token_for_phase8";

  function buildApp() {
    const session: SessionRecord = {
      id: "sess_p8",
      userId: "usr_owner_1",
      csrfToken: "csrf_p8",
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

    const dashboardRepo: DashboardAndReportsRepository = {
      async getDashboardSummary() {
        return {
          account: {
            login: "12345678",
            server: "Exness-MT5Demo",
            mode: "DEMO",
            accountType: "CENT",
            accountCurrency: "USC",
            currencyScale: 100,
            balance: "10250.0000",
            equity: "10290.0000",
            freeMargin: "10100.0000",
            usdEquivalent: {
              balance: "102.50",
              equity: "102.90",
              freeMargin: "101.00",
            },
          },
          controls: { isPaused: false, killSwitchActive: false },
          counts: { openPositions: 1, pendingProposals: 2 },
          risk: {
            daily: {
              startingEquity: "10000",
              currentEquity: "10290",
              lossLimit: "1.00",
              drawdownPercent: 0,
              isTripped: false,
            },
            weekly: {
              startingEquity: "10000",
              currentEquity: "10290",
              lossLimit: "2.00",
              drawdownPercent: 0,
              isTripped: false,
              resetAt: null,
            },
          },
        };
      },
      async getClosedTradesForReport() {
        return {
          accountCurrency: "USC",
          currencyScale: 100,
          trades: [
            {
              id: "p1",
              symbol: "XAUUSDc",
              side: "BUY",
              volume: "0.09",
              profit: "100.00",
              commission: "-0.63",
              swap: "0.00",
              closedAt: "2026-11-20T12:00:00.000Z",
            },
            {
              id: "p2",
              symbol: "XAUUSDc",
              side: "SELL",
              volume: "0.09",
              profit: "-40.00",
              commission: "-0.63",
              swap: "-0.50",
              closedAt: "2026-11-21T12:00:00.000Z",
            },
            {
              id: "p3",
              symbol: "XAUUSDc",
              side: "BUY",
              volume: "0.09",
              profit: "90.00",
              commission: "-0.63",
              swap: "0.00",
              closedAt: "2026-11-22T12:00:00.000Z",
            },
          ],
        };
      },
      async listAuditLogs() {
        return [
          {
            id: "aud_1",
            actorId: "usr_owner_1",
            actorEmail: "owner@local.dev",
            action: "PROPOSAL_APPROVED",
            entityType: "TradeProposal",
            entityId: "prop_1",
            metadata: { symbol: "XAUUSDc" },
            ip: "127.0.0.1",
            createdAt: new Date().toISOString(),
          },
        ];
      },
    };

    return createApp({
      config: testConfig,
      authStore,
      dashboardRepo,
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

  it("serves dashboard summary, Cent performance metrics, and audit logs", async () => {
    const app = buildApp();

    const sumRes = await request(app)
      .get("/api/v1/dashboard/summary")
      .set("Cookie", [`ta_session=${sessionToken}`]);
    expect(sumRes.status).toBe(200);
    expect(sumRes.body.account.usdEquivalent.balance).toBe("102.50");
    expect(sumRes.body.counts.openPositions).toBe(1);

    const repRes = await request(app)
      .get("/api/v1/reports/performance")
      .set("Cookie", [`ta_session=${sessionToken}`]);
    expect(repRes.status).toBe(200);
    expect(repRes.body.report.totalTrades).toBe(3);
    expect(repRes.body.report.wins).toBe(2);
    expect(repRes.body.report.losses).toBe(1);
    expect(repRes.body.report.winRatePercent).toBe(66.67);
    expect(Number(repRes.body.report.netPnlUsd)).toBeCloseTo(1.48, 2);

    const audRes = await request(app)
      .get("/api/v1/audit-logs")
      .set("Cookie", [`ta_session=${sessionToken}`]);
    expect(audRes.status).toBe(200);
    expect(audRes.body.logs).toHaveLength(1);
  });
});

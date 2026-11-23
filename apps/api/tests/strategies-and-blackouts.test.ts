import { describe, it, expect, beforeEach } from "vitest";
import request from "supertest";
import { createApp } from "../src/app.js";
import { loadEnv } from "../src/config/env.js";
import { sha256 } from "../src/lib/crypto.js";
import type { AuthStore, SessionRecord } from "../src/middleware/auth.js";
import type {
  StrategyRecord,
  StrategyRepository,
} from "../src/routes/strategies.js";
import type {
  BlackoutRecord,
  BlackoutRepository,
} from "../src/routes/blackouts.js";

describe("Phase 3 API: strategies & blackout windows CRUD", () => {
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

  const sessionToken = "raw_owner_session_token_for_phase3";
  const csrfToken = "csrf_p3_valid";
  let strategies: StrategyRecord[] = [];
  let blackouts: BlackoutRecord[] = [];
  let audits: string[] = [];

  beforeEach(() => {
    strategies = [
      {
        id: "strat_1",
        name: "xauusd_v1",
        version: "1.0.0",
        timeframe: "M15",
        parameters: { emaFast: 20, emaMid: 50, targetRewardRisk: 2.0 },
        isActive: true,
        updatedAt: new Date().toISOString(),
      },
    ];
    blackouts = [];
    audits = [];
  });

  function buildApp() {
    const session: SessionRecord = {
      id: "sess_p3",
      userId: "usr_owner_1",
      csrfToken,
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

    const strategyRepo: StrategyRepository = {
      async listStrategies() {
        return strategies;
      },
      async findStrategyById(id) {
        return strategies.find((s) => s.id === id) ?? null;
      },
      async updateStrategy(id, input) {
        const s = strategies.find((x) => x.id === id)!;
        if (input.isActive !== undefined) s.isActive = input.isActive;
        if (input.parameters !== undefined) s.parameters = input.parameters;
        s.updatedAt = new Date().toISOString();
        return s;
      },
      async writeAuditLog(input) {
        audits.push(input.action);
      },
      async notifySettingsUpdated() {},
    };

    const blackoutRepo: BlackoutRepository = {
      async listBlackouts() {
        return blackouts;
      },
      async createBlackout(input) {
        const b: BlackoutRecord = {
          id: `bo_${blackouts.length + 1}`,
          title: input.title,
          reason: input.reason ?? null,
          startsAt: input.startsAt.toISOString(),
          endsAt: input.endsAt.toISOString(),
          isActive: input.isActive,
          createdById: input.createdById ?? null,
          createdAt: new Date().toISOString(),
        };
        blackouts.push(b);
        return b;
      },
      async deleteBlackout(id) {
        const prev = blackouts.length;
        blackouts = blackouts.filter((b) => b.id !== id);
        return blackouts.length < prev;
      },
      async writeAuditLog(input) {
        audits.push(input.action);
      },
      async notifySettingsUpdated() {},
    };

    return createApp({
      config: testConfig,
      authStore,
      strategyRepo,
      blackoutRepo,
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

  it("lists and updates strategy parameters with CSRF & AuditLog", async () => {
    const app = buildApp();

    const listRes = await request(app)
      .get("/api/v1/strategies")
      .set("Cookie", [`ta_session=${sessionToken}`]);
    expect(listRes.status).toBe(200);
    expect(listRes.body.strategies).toHaveLength(1);

    const noCsrf = await request(app)
      .patch("/api/v1/strategies/strat_1")
      .set("Cookie", [`ta_session=${sessionToken}`])
      .send({ isActive: false });
    expect(noCsrf.status).toBe(403);

    const okRes = await request(app)
      .patch("/api/v1/strategies/strat_1")
      .set("Cookie", [`ta_session=${sessionToken}`])
      .set("x-csrf-token", csrfToken)
      .send({ parameters: { targetRewardRisk: 2.5 } });

    expect(okRes.status).toBe(200);
    expect(okRes.body.strategy.parameters.targetRewardRisk).toBe(2.5);
    expect(audits).toContain("STRATEGY_UPDATED");
  });

  it("creates, validates, lists, and deletes blackout windows", async () => {
    const app = buildApp();

    // Invalid range (endsAt <= startsAt) must return 400
    const badRes = await request(app)
      .post("/api/v1/blackouts")
      .set("Cookie", [`ta_session=${sessionToken}`])
      .set("x-csrf-token", csrfToken)
      .send({
        title: "US CPI",
        startsAt: "2026-11-24T13:30:00.000Z",
        endsAt: "2026-11-24T13:00:00.000Z",
      });
    expect(badRes.status).toBe(400);

    const createRes = await request(app)
      .post("/api/v1/blackouts")
      .set("Cookie", [`ta_session=${sessionToken}`])
      .set("x-csrf-token", csrfToken)
      .send({
        title: "US NFP High Impact",
        reason: "Exness HMR + extreme gold volatility",
        startsAt: "2026-11-24T13:15:00.000Z",
        endsAt: "2026-11-24T13:45:00.000Z",
      });
    expect(createRes.status).toBe(201);
    const id = createRes.body.blackout.id as string;

    const listRes = await request(app)
      .get("/api/v1/blackouts")
      .set("Cookie", [`ta_session=${sessionToken}`]);
    expect(listRes.body.blackouts).toHaveLength(1);

    const delRes = await request(app)
      .delete(`/api/v1/blackouts/${id}`)
      .set("Cookie", [`ta_session=${sessionToken}`])
      .set("x-csrf-token", csrfToken);
    expect(delRes.status).toBe(200);
    expect(audits).toContain("BLACKOUT_CREATED");
    expect(audits).toContain("BLACKOUT_DELETED");
  });
});

import { describe, it, expect, beforeEach } from "vitest";
import request from "supertest";
import { createApp } from "../src/app.js";
import { loadEnv } from "../src/config/env.js";
import { sha256 } from "../src/lib/crypto.js";
import type { AuthStore, SessionRecord } from "../src/middleware/auth.js";
import type {
  TradingSettingsRecord,
  TradingSettingsRepository,
} from "../src/routes/settings.js";
import type {
  RiskResetRepository,
  WeeklyRiskRecord,
} from "../src/routes/risk.js";

describe("Phase 4 API: trading settings bounds & weekly loss limit manual reset", () => {
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

  const sessionToken = "raw_owner_session_token_for_phase4";
  const csrfToken = "csrf_p4_valid";
  let settings: TradingSettingsRecord;
  let weekly: WeeklyRiskRecord;
  let activeAccount: { mode: "DEMO" | "LIVE"; liveUnlockedAt: string | null };
  let audits: string[] = [];

  beforeEach(() => {
    audits = [];
    activeAccount = { mode: "DEMO", liveUnlockedAt: null };
    settings = {
      id: "set_1",
      symbol: "XAUUSDc",
      riskPercent: "0.25",
      dailyLossLimit: "1.00",
      weeklyLossLimit: "2.00",
      maxPositions: 1,
      maxPendingProposals: 3,
      minRewardRisk: "2.00",
      maxSpread: "0.50",
      maxDeviationPoints: 30,
      commissionPerLot: "0.00",
      approvalExpirySeconds: 120,
      postRejectionCooldownMinutes: 15,
      rolloverBlackoutMinutes: 60,
      sessionWindows: {},
      aiFallbackMode: "DETERMINISTIC_ONLY",
      isPaused: false,
      killSwitchActive: false,
      cooldownUntil: null,
      updatedAt: new Date().toISOString(),
    };
    weekly = {
      id: "wk_1",
      brokerAccountId: "acc_1",
      weekStart: "2026-11-23",
      startingEquity: "10000.0000",
      currentEquity: "9780.0000",
      realizedPnl: "-220.0000",
      unrealizedPnl: "0.0000",
      lossLimit: "2.0000",
      isTripped: true,
      trippedAt: new Date().toISOString(),
      resetByUserId: null,
      resetAt: null,
      resetNote: null,
    };
  });

  function buildApp() {
    const session: SessionRecord = {
      id: "sess_p4",
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

    const settingsRepo: TradingSettingsRepository = {
      async getSettings() {
        return settings;
      },
      async getActiveAccountMode() {
        return activeAccount;
      },
      async updateSettings(_id, patch) {
        settings = { ...settings, ...patch, updatedAt: new Date().toISOString() };
        return settings;
      },
      async writeAuditLog(input) {
        audits.push(input.action);
      },
      async notifySettingsUpdated() {},
    };

    const riskRepo: RiskResetRepository = {
      async getLatestWeeklySnapshot() {
        return weekly;
      },
      async resetWeeklySnapshot({ userId, note, resetAt }) {
        weekly = {
          ...weekly,
          isTripped: false,
          resetByUserId: userId,
          resetAt: resetAt.toISOString(),
          resetNote: note,
        };
        return weekly;
      },
      async writeAuditLog(input) {
        audits.push(input.action);
      },
      async notifySettingsUpdated() {},
    };

    return createApp({
      config: testConfig,
      authStore,
      settingsRepo,
      riskRepo,
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

  it("enforces strict safe bounds on PATCH /api/v1/settings/trading", async () => {
    const app = buildApp();

    // Reject riskPercent > 0.50%
    const highRisk = await request(app)
      .patch("/api/v1/settings/trading")
      .set("Cookie", [`ta_session=${sessionToken}`])
      .set("x-csrf-token", csrfToken)
      .send({ riskPercent: "2.50" });
    expect(highRisk.status).toBe(400);

    // Reject maxPositions > 1 in v1
    const multiPos = await request(app)
      .patch("/api/v1/settings/trading")
      .set("Cookie", [`ta_session=${sessionToken}`])
      .set("x-csrf-token", csrfToken)
      .send({ maxPositions: 3 });
    expect(multiPos.status).toBe(400);

    // Valid update succeeds and logs audit
    const ok = await request(app)
      .patch("/api/v1/settings/trading")
      .set("Cookie", [`ta_session=${sessionToken}`])
      .set("x-csrf-token", csrfToken)
      .send({ riskPercent: "0.40", commissionPerLot: "0.07", maxDailyTrades: 5 });
    expect(ok.status).toBe(200);
    expect(ok.body.settings.riskPercent).toBe("0.40");
    expect(ok.body.settings.maxDailyTrades).toBe(5);
    expect(audits).toContain("TRADING_SETTINGS_UPDATED");
  });

  it("requires a review note to reset a tripped weekly loss limit", async () => {
    const app = buildApp();

    const shortNote = await request(app)
      .post("/api/v1/risk/weekly/reset")
      .set("Cookie", [`ta_session=${sessionToken}`])
      .set("x-csrf-token", csrfToken)
      .send({ note: "too short" });
    expect(shortNote.status).toBe(400);

    const ok = await request(app)
      .post("/api/v1/risk/weekly/reset")
      .set("Cookie", [`ta_session=${sessionToken}`])
      .set("x-csrf-token", csrfToken)
      .send({
        note: "Reviewed 2 losing demo trades; spread filter adjusted before resuming.",
      });
    expect(ok.status).toBe(200);
    expect(ok.body.weeklySnapshot.isTripped).toBe(false);
    expect(ok.body.weeklySnapshot.resetByUserId).toBe("usr_owner_1");
    expect(audits).toContain("WEEKLY_LOSS_LIMIT_RESET");

    // Second reset when no longer tripped returns 409
    const again = await request(app)
      .post("/api/v1/risk/weekly/reset")
      .set("Cookie", [`ta_session=${sessionToken}`])
      .set("x-csrf-token", csrfToken)
      .send({ note: "Trying to reset again when already cleared" });
    expect(again.status).toBe(409);
  });

  it("allows demoAutoExecute on DEMO accounts but hard-blocks it (403) on LIVE accounts", async () => {
    const app = buildApp();

    // DEMO account: can enable demoAutoExecute
    const enableOnDemo = await request(app)
      .patch("/api/v1/settings/trading")
      .set("Cookie", [`ta_session=${sessionToken}`])
      .set("x-csrf-token", csrfToken)
      .send({ demoAutoExecute: true });
    expect(enableOnDemo.status).toBe(200);
    expect(enableOnDemo.body.settings.demoAutoExecute).toBe(true);

    // LIVE account: hard-blocked with 403
    activeAccount = {
      mode: "LIVE",
      liveUnlockedAt: new Date().toISOString(),
    };
    const enableOnLive = await request(app)
      .patch("/api/v1/settings/trading")
      .set("Cookie", [`ta_session=${sessionToken}`])
      .set("x-csrf-token", csrfToken)
      .send({ demoAutoExecute: true });
    expect(enableOnLive.status).toBe(403);
    expect(enableOnLive.body.error).toMatch(/LIVE/i);
  });
});

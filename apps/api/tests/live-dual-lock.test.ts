import { describe, it, expect, beforeEach } from "vitest";
import request from "supertest";
import { createApp } from "../src/app.js";
import { loadEnv } from "../src/config/env.js";
import { hashPassword, sha256 } from "../src/lib/crypto.js";
import type { AuthStore, SessionRecord } from "../src/middleware/auth.js";
import {
  LIVE_UNLOCK_PHRASE,
  type AccountQueryRepository,
  type BrokerAccountSummary,
} from "../src/routes/account.js";

describe("Phase 10 API: Live Mode Dual-Lock unlock & lock-back-to-demo", () => {
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

  const sessionToken = "raw_owner_session_token_for_phase10";
  const csrfToken = "csrf_p10_valid";
  let ownerHash = "";
  let account: BrokerAccountSummary;
  let audits: string[] = [];

  beforeEach(async () => {
    ownerHash = await hashPassword("OwnerLivePass123!");
    audits = [];
    account = {
      id: "acc_1",
      broker: "Exness",
      platform: "MT5",
      login: "12345678",
      server: "Exness-MT5Real",
      accountCurrency: "USC",
      currencyScale: 100,
      accountType: "CENT",
      marginMode: "HEDGING",
      magicNumber: 260101,
      mode: "DEMO",
      balance: "10000.0000",
      equity: "10000.0000",
      freeMargin: "10000.0000",
      symbolSpecs: null,
      liveUnlockedAt: null,
      liveUnlockedBy: null,
      isEnabled: true,
      lastSyncedAt: new Date().toISOString(),
    };
  });

  function buildApp() {
    const session: SessionRecord = {
      id: "sess_p10",
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

    const accountRepo: AccountQueryRepository = {
      async getActiveBrokerAccount() {
        return account;
      },
      async getUserPasswordHash() {
        return ownerHash;
      },
      async unlockLiveMode({ userId, unlockedAt }) {
        account = {
          ...account,
          mode: "LIVE",
          liveUnlockedAt: unlockedAt.toISOString(),
          liveUnlockedBy: userId,
        };
        return account;
      },
      async lockDemoMode() {
        account = {
          ...account,
          mode: "DEMO",
          liveUnlockedAt: null,
          liveUnlockedBy: null,
        };
        return account;
      },
      async writeAuditLog(input) {
        audits.push(input.action);
      },
      async publishSseEvent() {},
    };

    return createApp({
      config: testConfig,
      authStore,
      accountRepo,
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

  it("requires password re-auth, exact typed confirmation, and CSRF to unlock LIVE, and locks back cleanly", async () => {
    const app = buildApp();

    // Wrong typed phrase -> 400
    const badPhrase = await request(app)
      .post("/api/v1/account/unlock-live")
      .set("Cookie", [`ta_session=${sessionToken}`])
      .set("x-csrf-token", csrfToken)
      .send({
        password: "OwnerLivePass123!",
        confirmationText: "yes unlock",
        demoSignoffConfirmed: true,
        acceptCapitalLossRisk: true,
      });
    expect(badPhrase.status).toBe(400);

    // Wrong password -> 401
    const badPass = await request(app)
      .post("/api/v1/account/unlock-live")
      .set("Cookie", [`ta_session=${sessionToken}`])
      .set("x-csrf-token", csrfToken)
      .send({
        password: "WrongPassword999!",
        confirmationText: LIVE_UNLOCK_PHRASE,
        demoSignoffConfirmed: true,
        acceptCapitalLossRisk: true,
      });
    expect(badPass.status).toBe(401);

    // Valid unlock succeeds
    const ok = await request(app)
      .post("/api/v1/account/unlock-live")
      .set("Cookie", [`ta_session=${sessionToken}`])
      .set("x-csrf-token", csrfToken)
      .send({
        password: "OwnerLivePass123!",
        confirmationText: LIVE_UNLOCK_PHRASE,
        demoSignoffConfirmed: true,
        acceptCapitalLossRisk: true,
      });
    expect(ok.status).toBe(200);
    expect(ok.body.account.mode).toBe("LIVE");
    expect(ok.body.account.liveUnlockedBy).toBe("usr_owner_1");
    expect(audits).toContain("LIVE_MODE_UNLOCKED");

    // Instant lock-back-to-DEMO clears unlock metadata
    const lockRes = await request(app)
      .post("/api/v1/account/lock-demo")
      .set("Cookie", [`ta_session=${sessionToken}`])
      .set("x-csrf-token", csrfToken);
    expect(lockRes.status).toBe(200);
    expect(lockRes.body.account.mode).toBe("DEMO");
    expect(lockRes.body.account.liveUnlockedAt).toBeNull();
    expect(audits).toContain("LIVE_MODE_LOCKED_TO_DEMO");
  });
});

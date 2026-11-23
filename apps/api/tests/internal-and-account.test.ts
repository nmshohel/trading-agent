import { describe, it, expect, beforeEach } from "vitest";
import request from "supertest";
import { createApp } from "../src/app.js";
import { loadEnv } from "../src/config/env.js";
import { sha256 } from "../src/lib/crypto.js";
import type { AuthStore, SessionRecord } from "../src/middleware/auth.js";
import type {
  AccountSyncPayload,
  HeartbeatPayload,
  InternalWorkerRepository,
  SystemEventPayload,
} from "../src/routes/internal.js";
import type {
  AccountQueryRepository,
  BrokerAccountSummary,
} from "../src/routes/account.js";

describe("Phase 1 API: internal worker endpoints, account sync & health", () => {
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

  const sessionToken = "raw_owner_session_token_for_phase1";
  let latestHeartbeat: HeartbeatPayload | null = null;
  let savedAccount: BrokerAccountSummary | null = null;
  let events: SystemEventPayload[] = [];
  let publishedSse: unknown[] = [];

  beforeEach(() => {
    latestHeartbeat = null;
    savedAccount = null;
    events = [];
    publishedSse = [];
  });

  function buildApp() {
    const session: SessionRecord = {
      id: "sess_1",
      userId: "usr_owner_1",
      csrfToken: "csrf_1",
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
        return {
          fetchedAt: new Date().toISOString(),
          killSwitchActive: false,
          isPaused: false,
          settings: { symbol: "XAUUSDc", riskPercent: "0.25" },
          strategies: [{ name: "xauusd_v1", version: "1.0.0", isActive: true }],
          blackouts: [],
          account: savedAccount,
          dailySnapshot: null,
          weeklySnapshot: null,
        };
      },
      async findBrokerAccount() {
        if (!savedAccount) return null;
        return {
          id: savedAccount.id,
          mode: savedAccount.mode,
          liveUnlockedAt: null,
        };
      },
      async upsertBrokerAccount(payload: AccountSyncPayload) {
        savedAccount = {
          id: "acc_1",
          broker: payload.broker,
          platform: payload.platform,
          login: payload.login,
          server: payload.server,
          accountCurrency: payload.accountCurrency,
          currencyScale: payload.currencyScale,
          accountType: payload.accountType,
          marginMode: payload.marginMode,
          magicNumber: payload.magicNumber,
          mode: payload.mode,
          balance: payload.balance,
          equity: payload.equity,
          freeMargin: payload.freeMargin,
          symbolSpecs: payload.symbolSpecs,
          isEnabled: true,
          lastSyncedAt: new Date().toISOString(),
        };
        return savedAccount as unknown as Record<string, unknown>;
      },
      async upsertMarketSnapshot(payload) {
        return { id: "snap_1", candleTime: payload.candleTime };
      },
      async createSystemEvent(payload) {
        events.push(payload);
        return { id: `evt_${events.length}`, createdAt: new Date() };
      },
      async saveHeartbeat(payload) {
        latestHeartbeat = payload;
      },
      async publishSseEvent(event) {
        publishedSse.push(event);
      },
    };

    const accountRepo: AccountQueryRepository = {
      async getActiveBrokerAccount() {
        return savedAccount;
      },
    };

    return createApp({
      config: testConfig,
      authStore,
      internalRepo,
      accountRepo,
      healthChecker: {
        async checkDatabase() {
          return true;
        },
        async checkRedis() {
          return true;
        },
        async getWorkerHeartbeat() {
          return latestHeartbeat;
        },
      },
    });
  }

  const validCentAccountSync: AccountSyncPayload = {
    broker: "Exness",
    platform: "MT5",
    login: "12345678",
    server: "Exness-MT5Demo",
    accountCurrency: "USC",
    currencyScale: 100,
    accountType: "CENT",
    marginMode: "HEDGING",
    magicNumber: 260101,
    mode: "DEMO",
    balance: "10000.0000",
    equity: "10050.0000",
    freeMargin: "9950.0000",
    symbolSpecs: {
      symbol: "XAUUSDc",
      volumeMin: "0.01",
      volumeMax: "200.00",
      volumeStep: "0.01",
      tradeContractSize: "100.0",
      tradeTickSize: "0.01",
      tradeTickValue: "1.0",
      point: "0.01",
      digits: 2,
      tradeStopsLevel: 10,
      tradeFreezeLevel: 0,
      fillingMode: "IOC",
      fillingModeFlags: 2,
      expirationModeFlags: 15,
      tradeMode: 4,
    },
  };

  it("syncs Cent DEMO account and exposes USD equivalent on /api/v1/account", async () => {
    const app = buildApp();

    const syncRes = await request(app)
      .post("/api/internal/v1/account/sync")
      .set("Authorization", `Bearer ${testConfig.WORKER_AUTH_TOKEN}`)
      .send(validCentAccountSync);

    expect(syncRes.status).toBe(200);
    expect(syncRes.body.account.accountCurrency).toBe("USC");

    const accRes = await request(app)
      .get("/api/v1/account")
      .set("Cookie", [`ta_session=${sessionToken}`]);

    expect(accRes.status).toBe(200);
    expect(accRes.body.account.accountType).toBe("CENT");
    expect(accRes.body.account.usdEquivalent).toEqual({
      balance: "100.00",
      equity: "100.50",
      freeMargin: "99.50",
    });
  });

  it("blocks unlocked LIVE account sync and logs a CRITICAL SystemEvent", async () => {
    const app = buildApp();

    const res = await request(app)
      .post("/api/internal/v1/account/sync")
      .set("Authorization", `Bearer ${testConfig.WORKER_AUTH_TOKEN}`)
      .send({ ...validCentAccountSync, mode: "LIVE" });

    expect(res.status).toBe(403);
    expect(events).toHaveLength(1);
    expect(events[0]?.severity).toBe("CRITICAL");
  });

  it("records worker heartbeat, publishes SSE event, and reports worker up on /api/v1/system/health", async () => {
    const app = buildApp();

    const hbRes = await request(app)
      .post("/api/internal/v1/heartbeat")
      .set("Authorization", `Bearer ${testConfig.WORKER_AUTH_TOKEN}`)
      .send({
        workerId: "win-mt5-1",
        mt5Connected: true,
        terminalTradeAllowed: true,
        accountTradeAllowed: true,
        accountLogin: "12345678",
        accountServer: "Exness-MT5Demo",
        accountMode: "DEMO",
        symbol: "XAUUSDc",
        serverTimeOffsetSeconds: 0,
        timestamp: new Date().toISOString(),
      });

    expect(hbRes.status).toBe(200);
    expect(publishedSse).toHaveLength(1);

    const healthRes = await request(app)
      .get("/api/v1/system/health")
      .set("Cookie", [`ta_session=${sessionToken}`]);

    expect(healthRes.status).toBe(200);
    expect(healthRes.body.services.worker).toBe("up");
    expect(healthRes.body.services.mt5).toBe("connected");
    expect(healthRes.body.workerDetails.terminalTradeAllowed).toBe(true);
  });

  it("returns runtime-state for authenticated worker and records events", async () => {
    const app = buildApp();

    const stateRes = await request(app)
      .get("/api/internal/v1/runtime-state")
      .set("Authorization", `Bearer ${testConfig.WORKER_AUTH_TOKEN}`);
    expect(stateRes.status).toBe(200);
    expect(stateRes.body.killSwitchActive).toBe(false);

    const evtRes = await request(app)
      .post("/api/internal/v1/events")
      .set("Authorization", `Bearer ${testConfig.WORKER_AUTH_TOKEN}`)
      .send({
        service: "trading-worker",
        severity: "INFO",
        message: "Connected to MT5 demo",
        metadata: { symbol: "XAUUSDc" },
      });
    expect(evtRes.status).toBe(201);
    expect(evtRes.body.id).toBeTruthy();
  });
});

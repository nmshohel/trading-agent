import { describe, it, expect, beforeEach } from "vitest";
import request from "supertest";
import type { ClosePositionCommand } from "@trading-agent/shared";
import { createApp } from "../src/app.js";
import { loadEnv } from "../src/config/env.js";
import { sha256 } from "../src/lib/crypto.js";
import type { AuthStore, SessionRecord } from "../src/middleware/auth.js";
import type {
  OrderRecord,
  PositionRecord,
  PositionsAndOrdersRepository,
} from "../src/routes/positions.js";
import type {
  InternalWorkerRepository,
  OrderResultPayload,
} from "../src/routes/internal.js";

describe("Phase 7 API: manual position close, idempotency & order result sync", () => {
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

  const sessionToken = "raw_owner_session_token_for_phase7";
  const csrfToken = "csrf_p7_valid";

  let positions: PositionRecord[] = [];
  let orders: OrderRecord[] = [];
  let closeCommands: ClosePositionCommand[] = [];
  let usedCloseKeys = new Set<string>();
  let sseEvents: string[] = [];
  let audits: string[] = [];

  beforeEach(() => {
    positions = [
      {
        id: "pos_1",
        brokerAccountId: "acc_1",
        accountLogin: "12345678",
        accountCurrency: "USC",
        currencyScale: 100,
        brokerPositionId: "99887766",
        symbol: "XAUUSDc",
        side: "BUY",
        volume: "0.09",
        openPrice: "2650.20",
        currentPrice: "2654.20",
        closePrice: null,
        stopLoss: "2645.20",
        takeProfit: "2660.20",
        profit: "36.0000",
        commission: "-0.6300",
        swap: "0.0000",
        magicNumber: 260101,
        isBotManaged: true,
        status: "OPEN",
        closeRequestedAt: null,
        openedAt: new Date().toISOString(),
        closedAt: null,
      },
    ];
    orders = [];
    closeCommands = [];
    usedCloseKeys = new Set();
    sseEvents = [];
    audits = [];
  });

  function buildApp() {
    const session: SessionRecord = {
      id: "sess_p7",
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

    const positionsRepo: PositionsAndOrdersRepository = {
      async listPositions({ status, limit }) {
        return positions
          .filter((p) => !status || p.status === status)
          .slice(0, limit);
      },
      async listOrders(limit) {
        return orders.slice(0, limit);
      },
      async requestManualCloseAtomic({ positionId, idempotencyKey, now }) {
        if (usedCloseKeys.has(idempotencyKey)) {
          return { ok: false, code: 409, error: "Duplicate idempotencyKey" };
        }
        const p = positions.find((x) => x.id === positionId);
        if (!p) return { ok: false, code: 404, error: "Not found" };
        if (p.status !== "OPEN") {
          return { ok: false, code: 409, error: `Already ${p.status}` };
        }
        usedCloseKeys.add(idempotencyKey);
        p.status = "CLOSING";
        p.closeRequestedAt = now.toISOString();
        return { ok: true, position: p };
      },
      async enqueueCloseCommand(cmd) {
        closeCommands.push(cmd);
        return `stream-close-${closeCommands.length}`;
      },
      async writeAuditLog(input) {
        audits.push(input.action);
      },
      async publishSseEvent(ev) {
        sseEvents.push(ev.type);
      },
    };

    const internalRepo: InternalWorkerRepository = {
      async getRuntimeState() {
        return { killSwitchActive: true };
      },
      async findBrokerAccount() {
        return null;
      },
      async upsertBrokerAccount() {
        return {};
      },
      async upsertMarketSnapshot(p) {
        return { id: "snap_1", candleTime: p.candleTime };
      },
      async getStuckReconciliation() {
        return {
          stuckProposals: [],
          stuckPositions: positions
            .filter((p) => p.status === "CLOSING")
            .map((p) => ({ ...p })),
        };
      },
      async recordOrderResult(payload: OrderResultPayload) {
        orders.push({
          id: `ord_${orders.length + 1}`,
          proposalId: payload.proposalId ?? null,
          positionId: payload.positionId ?? null,
          actionType: payload.actionType,
          brokerOrderId: payload.brokerOrderId ?? null,
          brokerDealId: payload.brokerDealId ?? null,
          symbol: payload.symbol,
          side: payload.side,
          volume: payload.volume,
          requestedPrice: payload.requestedPrice ?? null,
          entryPrice: payload.entryPrice ?? null,
          stopLoss: payload.stopLoss ?? null,
          takeProfit: payload.takeProfit ?? null,
          fillingMode: payload.fillingMode ?? null,
          deviationPoints: payload.deviationPoints ?? null,
          retcode: payload.retcode ?? null,
          brokerComment: payload.brokerComment ?? null,
          status: payload.orderStatus,
          createdAt: new Date().toISOString(),
        });
        if (payload.positionId && payload.positionStatus) {
          const p = positions.find((x) => x.id === payload.positionId);
          if (p) {
            p.status = payload.positionStatus;
            p.closePrice = payload.closePrice ?? null;
          }
        }
        return {
          orderId: `ord_${orders.length}`,
          positionId: payload.positionId ?? null,
        };
      },
      async syncPositions(payload) {
        return { syncedCount: payload.positions.length };
      },
      async createSystemEvent() {
        return { id: "evt_1", createdAt: new Date() };
      },
      async saveHeartbeat() {},
      async publishSseEvent(ev) {
        sseEvents.push(ev.type);
      },
    };

    return createApp({
      config: testConfig,
      authStore,
      positionsRepo,
      internalRepo,
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

  it("enqueues audited manual close once, rejects double-click, and reconciles CLOSE result", async () => {
    const app = buildApp();

    // GET /positions exposes USD equivalent for Cent account
    const listRes = await request(app)
      .get("/api/v1/positions")
      .set("Cookie", [`ta_session=${sessionToken}`]);
    expect(listRes.status).toBe(200);
    expect(listRes.body.positions[0].usdEquivalent.profit).toBe("0.36");

    // Manual close succeeds (202 Accepted) even while runtime killSwitchActive=true
    const close1 = await request(app)
      .post("/api/v1/positions/pos_1/close")
      .set("Cookie", [`ta_session=${sessionToken}`])
      .set("x-csrf-token", csrfToken)
      .send({ idempotencyKey: "close-idem-unique-1", reason: "Owner exit" });
    expect(close1.status).toBe(202);
    expect(close1.body.position.status).toBe("CLOSING");
    expect(closeCommands).toHaveLength(1);
    expect(closeCommands[0]?.type).toBe("CLOSE_POSITION");
    expect(audits).toContain("POSITION_MANUAL_CLOSE_REQUESTED");

    // Double-click / duplicate close returns 409
    const close2 = await request(app)
      .post("/api/v1/positions/pos_1/close")
      .set("Cookie", [`ta_session=${sessionToken}`])
      .set("x-csrf-token", csrfToken)
      .send({ idempotencyKey: "close-idem-unique-2" });
    expect(close2.status).toBe(409);
    expect(closeCommands).toHaveLength(1);

    // Worker posts reconciled CLOSE order result
    const resPost = await request(app)
      .post("/api/internal/v1/orders/result")
      .set("Authorization", `Bearer ${testConfig.WORKER_AUTH_TOKEN}`)
      .send({
        positionId: "pos_1",
        brokerAccountId: "acc_1",
        actionType: "CLOSE",
        idempotencyKey: "close-idem-unique-1",
        symbol: "XAUUSDc",
        side: "SELL",
        volume: "0.09",
        entryPrice: "2654.10",
        closePrice: "2654.10",
        realizedProfit: "35.1000",
        commission: "-0.6300",
        swap: "0.0000",
        retcode: 10009,
        brokerOrderId: "555111",
        brokerDealId: "666222",
        brokerPositionId: "99887766",
        orderStatus: "FILLED",
        positionStatus: "CLOSED",
        eventPayload: { manualClose: true },
      });
    expect(resPost.status).toBe(200);
    expect(sseEvents).toContain("order.updated");
    expect(sseEvents).toContain("position.closed");

    const ordersRes = await request(app)
      .get("/api/v1/orders")
      .set("Cookie", [`ta_session=${sessionToken}`]);
    expect(ordersRes.status).toBe(200);
    expect(ordersRes.body.orders).toHaveLength(1);
    expect(ordersRes.body.orders[0].actionType).toBe("CLOSE");
  });
});

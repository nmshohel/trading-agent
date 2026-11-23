import { describe, it, expect, beforeEach } from "vitest";
import request from "supertest";
import type { ExecuteProposalCommand } from "@trading-agent/shared";
import { createApp } from "../src/app.js";
import { loadEnv } from "../src/config/env.js";
import { sha256 } from "../src/lib/crypto.js";
import type { AuthStore, SessionRecord } from "../src/middleware/auth.js";
import type {
  ProposalRecord,
  ProposalWorkflowRepository,
} from "../src/routes/proposals.js";
import type { SystemControlRepository } from "../src/routes/system.js";
import type {
  CreateInternalProposalPayload,
  InternalWorkerRepository,
} from "../src/routes/internal.js";

describe("Phase 6 API: proposal approval, idempotency, atomic claim & kill switch", () => {
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

  const sessionToken = "raw_owner_session_token_for_phase6";
  const csrfToken = "csrf_p6_valid";

  let proposals: ProposalRecord[] = [];
  let usedIdempotencyKeys = new Set<string>();
  let queuedCommands: ExecuteProposalCommand[] = [];
  let killSwitchActive = false;
  let isPaused = false;
  let audits: string[] = [];

  beforeEach(() => {
    proposals = [];
    usedIdempotencyKeys = new Set();
    queuedCommands = [];
    killSwitchActive = false;
    isPaused = false;
    audits = [];
  });

  function buildApp() {
    const session: SessionRecord = {
      id: "sess_p6",
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

    const internalRepo: InternalWorkerRepository = {
      async getRuntimeState() {
        return { killSwitchActive, isPaused };
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
      async createProposal(payload: CreateInternalProposalPayload) {
        if (proposals.some((p) => p.dedupKey === payload.dedupKey)) {
          return { ok: false, code: 409, error: "Duplicate dedupKey" };
        }
        const rec: ProposalRecord = {
          id: `prop_${proposals.length + 1}`,
          dedupKey: payload.dedupKey,
          brokerAccountId: payload.brokerAccountId,
          accountLogin: "12345678",
          accountCurrency: "USC",
          currencyScale: 100,
          strategyId: payload.strategyId,
          strategyName: "xauusd_v1@1.0.0",
          symbol: payload.symbol,
          timeframe: payload.timeframe,
          direction: payload.direction,
          entry: payload.entry,
          stopLoss: payload.stopLoss,
          takeProfit: payload.takeProfit,
          volume: payload.volume,
          riskAmount: payload.riskAmount,
          riskPercent: payload.riskPercent,
          rewardRisk: payload.rewardRisk,
          estimatedMargin: payload.estimatedMargin ?? null,
          spreadAtProposal: payload.spreadAtProposal,
          rationale: payload.rationale,
          aiStatus: payload.aiStatus,
          aiModel: payload.aiModel ?? null,
          aiPromptVersion: payload.aiPromptVersion ?? null,
          aiLatencyMs: payload.aiLatencyMs ?? null,
          aiTokensUsed: payload.aiTokensUsed ?? null,
          aiRawResponse: payload.aiRawResponse ?? null,
          status: payload.status,
          rejectionReason: payload.rejectionReason ?? null,
          expiresAt: payload.expiresAt,
          claimedAt: null,
          createdAt: new Date().toISOString(),
          updatedAt: new Date().toISOString(),
        };
        proposals.push(rec);
        return { ok: true, proposal: rec as unknown as Record<string, unknown> };
      },
      async expirePendingProposals(now) {
        const expiredIds: string[] = [];
        for (const p of proposals) {
          if (p.status === "PENDING_APPROVAL" && Date.parse(p.expiresAt) <= now.getTime()) {
            p.status = "EXPIRED";
            expiredIds.push(p.id);
          }
        }
        return { expiredIds };
      },
      async claimProposalAtomic(proposalId, now) {
        const p = proposals.find((x) => x.id === proposalId);
        if (!p || p.status !== "APPROVED" || Date.parse(p.expiresAt) <= now.getTime()) {
          return { ok: false, code: 409, error: "Cannot claim proposal" };
        }
        p.status = "EXECUTING";
        p.claimedAt = now.toISOString();
        return { ok: true, proposal: p as unknown as Record<string, unknown> };
      },
      async createSystemEvent() {
        return { id: "evt_1", createdAt: new Date() };
      },
      async saveHeartbeat() {},
      async publishSseEvent() {},
    };

    const proposalRepo: ProposalWorkflowRepository = {
      async listProposals({ status, limit }) {
        return proposals
          .filter((p) => !status || p.status === status)
          .slice(0, limit);
      },
      async findProposalById(id) {
        return proposals.find((p) => p.id === id) ?? null;
      },
      async isSystemBlockedForNewOrders() {
        if (killSwitchActive) return { blocked: true, reason: "Kill switch is active" };
        if (isPaused) return { blocked: true, reason: "System paused" };
        return { blocked: false };
      },
      async hasApprovalIdempotencyKey(key) {
        return usedIdempotencyKeys.has(key);
      },
      async approveProposalAtomic({ proposalId, idempotencyKey, now }) {
        const p = proposals.find((x) => x.id === proposalId);
        if (!p) return { ok: false, code: 404, error: "Not found" };
        if (p.status !== "PENDING_APPROVAL") {
          return { ok: false, code: 409, error: "Not pending" };
        }
        if (Date.parse(p.expiresAt) <= now.getTime()) {
          return { ok: false, code: 409, error: "Proposal expired" };
        }
        usedIdempotencyKeys.add(idempotencyKey);
        p.status = "APPROVED";
        return { ok: true, proposal: p, approvalId: `appr_${usedIdempotencyKeys.size}` };
      },
      async rejectProposal({ proposalId }) {
        const p = proposals.find((x) => x.id === proposalId && x.status === "PENDING_APPROVAL");
        if (!p) return null;
        p.status = "REJECTED";
        return p;
      },
      async cancelProposal(proposalId) {
        const p = proposals.find((x) => x.id === proposalId && x.status === "PENDING_APPROVAL");
        if (!p) return null;
        p.status = "CANCELLED";
        return p;
      },
      async enqueueCommand(cmd) {
        queuedCommands.push(cmd);
        return `stream-${queuedCommands.length}`;
      },
      async writeAuditLog(input) {
        audits.push(input.action);
      },
      async publishSseEvent() {},
    };

    const systemControlRepo: SystemControlRepository = {
      async activateKillSwitch() {
        killSwitchActive = true;
        isPaused = true;
        let cancelledProposalsCount = 0;
        for (const p of proposals) {
          if (p.status === "PENDING_APPROVAL" || p.status === "APPROVED") {
            p.status = "CANCELLED";
            cancelledProposalsCount++;
          }
        }
        return { cancelledProposalsCount };
      },
      async setPaused(v) {
        isPaused = v;
      },
      async resumeSystem(clearKillSwitch) {
        if (killSwitchActive && !clearKillSwitch) {
          return { ok: false, error: "Pass clearKillSwitch=true" };
        }
        isPaused = false;
        if (clearKillSwitch) killSwitchActive = false;
        return { ok: true };
      },
      async writeAuditLog(input) {
        audits.push(input.action);
      },
      async publishSseEvent() {},
    };

    return createApp({
      config: testConfig,
      authStore,
      internalRepo,
      proposalRepo,
      systemControlRepo,
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

  const baseProposalPayload = () => ({
    dedupKey: "xauusd_v1:XAUUSDc:M15:2026-11-23T13:00:00Z:BUY",
    brokerAccountId: "acc_1",
    strategyId: "strat_1",
    symbol: "XAUUSDc",
    timeframe: "M15",
    direction: "BUY",
    entry: "2650.20",
    stopLoss: "2645.20",
    takeProfit: "2660.20",
    volume: "0.09",
    riskAmount: "47.8800",
    riskPercent: "0.4788",
    rewardRisk: "2.00",
    estimatedMargin: "13.5000",
    spreadAtProposal: "0.20",
    rationale: "H1 bullish + M15 pullback",
    aiStatus: "COMPLETED",
    status: "PENDING_APPROVAL",
    expiresAt: new Date(Date.now() + 120_000).toISOString(),
  });

  it("enforces single-use approval, idempotency, Redis Stream enqueue, and atomic worker claim", async () => {
    const app = buildApp();

    // 1. Worker creates proposal
    const createRes = await request(app)
      .post("/api/internal/v1/proposals")
      .set("Authorization", `Bearer ${testConfig.WORKER_AUTH_TOKEN}`)
      .send(baseProposalPayload());
    expect(createRes.status).toBe(201);
    const id = createRes.body.proposal.id as string;

    // Duplicate dedupKey rejected
    const dupCreate = await request(app)
      .post("/api/internal/v1/proposals")
      .set("Authorization", `Bearer ${testConfig.WORKER_AUTH_TOKEN}`)
      .send(baseProposalPayload());
    expect(dupCreate.status).toBe(409);

    // 2. Owner approves once
    const approveRes = await request(app)
      .post(`/api/v1/proposals/${id}/approve`)
      .set("Cookie", [`ta_session=${sessionToken}`])
      .set("x-csrf-token", csrfToken)
      .send({ idempotencyKey: "idem-approve-unique-1" });
    expect(approveRes.status).toBe(200);
    expect(queuedCommands).toHaveLength(1);
    expect(queuedCommands[0]?.type).toBe("EXECUTE_PROPOSAL");

    // 3. Replaying same idempotencyKey returns 409 and does NOT enqueue second command
    const replayRes = await request(app)
      .post(`/api/v1/proposals/${id}/approve`)
      .set("Cookie", [`ta_session=${sessionToken}`])
      .set("x-csrf-token", csrfToken)
      .send({ idempotencyKey: "idem-approve-unique-1" });
    expect(replayRes.status).toBe(409);
    expect(queuedCommands).toHaveLength(1);

    // 4. Worker atomically claims APPROVED -> EXECUTING once; second claim returns 409
    const claim1 = await request(app)
      .post(`/api/internal/v1/proposals/${id}/claim`)
      .set("Authorization", `Bearer ${testConfig.WORKER_AUTH_TOKEN}`);
    expect(claim1.status).toBe(200);
    expect(claim1.body.proposal.status).toBe("EXECUTING");

    const claim2 = await request(app)
      .post(`/api/internal/v1/proposals/${id}/claim`)
      .set("Authorization", `Bearer ${testConfig.WORKER_AUTH_TOKEN}`);
    expect(claim2.status).toBe(409);
  });

  it("cancels pending proposals and blocks approvals when kill switch activates", async () => {
    const app = buildApp();

    const createRes = await request(app)
      .post("/api/internal/v1/proposals")
      .set("Authorization", `Bearer ${testConfig.WORKER_AUTH_TOKEN}`)
      .send(baseProposalPayload());
    const id = createRes.body.proposal.id as string;

    const ksRes = await request(app)
      .post("/api/v1/system/kill-switch")
      .set("Cookie", [`ta_session=${sessionToken}`])
      .set("x-csrf-token", csrfToken)
      .send({ reason: "Emergency test" });
    expect(ksRes.status).toBe(200);
    expect(ksRes.body.cancelledProposalsCount).toBe(1);
    expect(audits).toContain("KILL_SWITCH_ACTIVATED");

    // Attempting to approve after kill switch must fail
    const approveRes = await request(app)
      .post(`/api/v1/proposals/${id}/approve`)
      .set("Cookie", [`ta_session=${sessionToken}`])
      .set("x-csrf-token", csrfToken)
      .send({ idempotencyKey: "idem-after-ks-1" });
    expect(approveRes.status).toBe(409);
  });

  it("expires overdue proposals via internal expiry job", async () => {
    const app = buildApp();

    await request(app)
      .post("/api/internal/v1/proposals")
      .set("Authorization", `Bearer ${testConfig.WORKER_AUTH_TOKEN}`)
      .send({
        ...baseProposalPayload(),
        expiresAt: new Date(Date.now() - 5000).toISOString(),
      });

    const expRes = await request(app)
      .post("/api/internal/v1/proposals/expire")
      .set("Authorization", `Bearer ${testConfig.WORKER_AUTH_TOKEN}`);
    expect(expRes.status).toBe(200);
    expect(expRes.body.expiredCount).toBe(1);
  });
});

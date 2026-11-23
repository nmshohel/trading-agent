import { describe, it, expect, beforeEach } from "vitest";
import request from "supertest";
import { createApp } from "../src/app.js";
import { loadEnv } from "../src/config/env.js";
import { hashPassword } from "../src/lib/crypto.js";
import type { AuthStore, SessionRecord } from "../src/middleware/auth.js";
import type { AuthRepository } from "../src/routes/auth.js";

describe("Phase 0 API foundation: health, auth, CSRF & worker token", () => {
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

  const sessions = new Map<string, SessionRecord>();
  let ownerPasswordHash = "";

  beforeEach(async () => {
    sessions.clear();
    ownerPasswordHash = await hashPassword("StrongPass123!");
  });

  function buildApp(opts?: { dbOk?: boolean; redisOk?: boolean }) {
    const authStore: AuthStore = {
      async findSessionByTokenHash(tokenHash) {
        return sessions.get(tokenHash) ?? null;
      },
    };

    const authRepo: AuthRepository = {
      async findUserByEmail(email) {
        if (email === "owner@local.dev") {
          return {
            id: "usr_owner_1",
            email: "owner@local.dev",
            passwordHash: ownerPasswordHash,
            role: "OWNER",
            isActive: true,
          };
        }
        return null;
      },
      async createSession(input) {
        const id = `sess_${sessions.size + 1}`;
        sessions.set(input.tokenHash, {
          id,
          userId: input.userId,
          csrfToken: input.csrfToken,
          expiresAt: input.expiresAt,
          user: {
            id: input.userId,
            email: "owner@local.dev",
            role: "OWNER",
            isActive: true,
          },
        });
        return { id };
      },
      async deleteSession(sessionId) {
        for (const [k, v] of sessions.entries()) {
          if (v.id === sessionId) sessions.delete(k);
        }
      },
      async writeAuditLog() {
        // noop in unit test
      },
    };

    return createApp({
      config: testConfig,
      authStore,
      authRepo,
      healthChecker: {
        async checkDatabase() {
          return opts?.dbOk ?? true;
        },
        async checkRedis() {
          return opts?.redisOk ?? true;
        },
        async getWorkerHeartbeat() {
          return null;
        },
      },
    });
  }

  it("exposes unauthenticated /health without sensitive fields", async () => {
    const app = buildApp();
    const res = await request(app).get("/health");
    expect(res.status).toBe(200);
    expect(res.body.status).toBe("ok");
    expect(res.body.timestamp).toBeTruthy();
  });

  it("protects /api/v1/system/health with authentication", async () => {
    const app = buildApp();
    const res = await request(app).get("/api/v1/system/health");
    expect(res.status).toBe(401);
  });

  it("supports login, /me, /system/health, CSRF-protected logout", async () => {
    const app = buildApp();
    const agent = request.agent(app);

    const loginRes = await agent
      .post("/api/v1/auth/login")
      .send({ email: "owner@local.dev", password: "StrongPass123!" });

    expect(loginRes.status).toBe(200);
    expect(loginRes.body.user.role).toBe("OWNER");
    expect(loginRes.body.csrfToken).toBeTruthy();

    const csrfToken = loginRes.body.csrfToken as string;

    const meRes = await agent.get("/api/v1/auth/me");
    expect(meRes.status).toBe(200);
    expect(meRes.body.user.email).toBe("owner@local.dev");

    const healthRes = await agent.get("/api/v1/system/health");
    expect(healthRes.status).toBe(200);
    expect(healthRes.body.services.database).toBe("up");
    expect(healthRes.body.services.redis).toBe("up");

    // Logout without CSRF must fail
    const badLogout = await agent.post("/api/v1/auth/logout");
    expect(badLogout.status).toBe(403);

    // Logout with valid CSRF succeeds
    const okLogout = await agent
      .post("/api/v1/auth/logout")
      .set("x-csrf-token", csrfToken);
    expect(okLogout.status).toBe(200);

    // Session is now revoked
    const afterLogout = await agent.get("/api/v1/auth/me");
    expect(afterLogout.status).toBe(401);
  });

  it("enforces Bearer WORKER_AUTH_TOKEN on /api/internal/v1/*", async () => {
    const app = buildApp();

    const noToken = await request(app).get("/api/internal/v1/ping");
    expect(noToken.status).toBe(401);

    const badToken = await request(app)
      .get("/api/internal/v1/ping")
      .set("Authorization", "Bearer wrong_token");
    expect(badToken.status).toBe(403);

    const goodToken = await request(app)
      .get("/api/internal/v1/ping")
      .set("Authorization", `Bearer ${testConfig.WORKER_AUTH_TOKEN}`);
    expect(goodToken.status).toBe(200);
    expect(goodToken.body.ok).toBe(true);
  });
});

import { Router, type RequestHandler } from "express";
import rateLimit from "express-rate-limit";
import { z } from "zod";
import type { UserRole } from "@trading-agent/shared";
import {
  generateRandomToken,
  sha256,
  verifyPassword,
} from "../lib/crypto.js";

const LoginSchema = z.object({
  email: z.string().trim().email(),
  password: z.string().min(8).max(128),
});

export interface AuthRepository {
  findUserByEmail(email: string): Promise<{
    id: string;
    email: string;
    passwordHash: string;
    role: UserRole;
    isActive: boolean;
  } | null>;
  createSession(input: {
    userId: string;
    tokenHash: string;
    csrfToken: string;
    ip?: string;
    userAgent?: string;
    expiresAt: Date;
  }): Promise<{ id: string }>;
  deleteSession(sessionId: string): Promise<void>;
  writeAuditLog(input: {
    actorId?: string;
    action: string;
    entityType: string;
    entityId?: string;
    metadata: Record<string, unknown>;
    ip?: string;
  }): Promise<void>;
}

export function createAuthRouter(opts: {
  repo: AuthRepository;
  requireAuth: RequestHandler;
  requireCsrf: RequestHandler;
  cookieName: string;
  sessionTtlHours: number;
  isProduction: boolean;
}) {
  const router = Router();

  const loginLimiter = rateLimit({
    windowMs: 15 * 60 * 1000,
    max: 20,
    standardHeaders: true,
    legacyHeaders: false,
    message: { error: "Too many login attempts, please try again later" },
  });

  router.post("/api/v1/auth/login", loginLimiter, async (req, res, next) => {
    try {
      const parsed = LoginSchema.parse(req.body);
      const user = await opts.repo.findUserByEmail(parsed.email.toLowerCase());

      if (!user || !user.isActive) {
        res.status(401).json({ error: "Invalid email or password" });
        return;
      }

      const valid = await verifyPassword(parsed.password, user.passwordHash);
      if (!valid) {
        res.status(401).json({ error: "Invalid email or password" });
        return;
      }

      const rawToken = generateRandomToken(32);
      const csrfToken = generateRandomToken(24);
      const expiresAt = new Date(
        Date.now() + opts.sessionTtlHours * 60 * 60 * 1000,
      );

      const session = await opts.repo.createSession({
        userId: user.id,
        tokenHash: sha256(rawToken),
        csrfToken,
        ip: req.ip,
        userAgent: req.header("user-agent"),
        expiresAt,
      });

      await opts.repo.writeAuditLog({
        actorId: user.id,
        action: "AUTH_LOGIN",
        entityType: "Session",
        entityId: session.id,
        metadata: { email: user.email },
        ip: req.ip,
      });

      res.cookie(opts.cookieName, rawToken, {
        httpOnly: true,
        secure: opts.isProduction,
        sameSite: "lax",
        path: "/",
        expires: expiresAt,
      });

      res.status(200).json({
        user: {
          id: user.id,
          email: user.email,
          role: user.role,
        },
        csrfToken,
        expiresAt: expiresAt.toISOString(),
      });
    } catch (err) {
      next(err);
    }
  });

  router.get("/api/v1/auth/me", opts.requireAuth, (req, res) => {
    res.status(200).json({
      user: req.authUser,
      csrfToken: req.csrfToken,
    });
  });

  router.post(
    "/api/v1/auth/logout",
    opts.requireAuth,
    opts.requireCsrf,
    async (req, res, next) => {
      try {
        if (req.sessionId) {
          await opts.repo.deleteSession(req.sessionId);
        }

        await opts.repo.writeAuditLog({
          actorId: req.authUser?.id,
          action: "AUTH_LOGOUT",
          entityType: "Session",
          entityId: req.sessionId,
          metadata: {},
          ip: req.ip,
        });

        res.clearCookie(opts.cookieName, {
          httpOnly: true,
          secure: opts.isProduction,
          sameSite: "lax",
          path: "/",
        });

        res.status(200).json({ ok: true });
      } catch (err) {
        next(err);
      }
    },
  );

  return router;
}

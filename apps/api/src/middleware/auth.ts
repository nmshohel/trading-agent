import type { Request, Response, NextFunction } from "express";
import type { UserRole } from "@trading-agent/shared";
import { sha256, timingSafeEqualStr } from "../lib/crypto.js";

export interface AuthUser {
  id: string;
  email: string;
  role: UserRole;
}

export interface SessionRecord {
  id: string;
  userId: string;
  csrfToken: string;
  expiresAt: Date;
  user: AuthUser & { isActive: boolean };
}

export interface AuthStore {
  findSessionByTokenHash(tokenHash: string): Promise<SessionRecord | null>;
}

declare global {
  namespace Express {
    interface Request {
      authUser?: AuthUser;
      sessionId?: string;
      csrfToken?: string;
    }
  }
}

export function createAuthMiddleware(opts: {
  authStore: AuthStore;
  cookieName: string;
  workerAuthToken: string;
}) {
  const requireAuth = async (
    req: Request,
    res: Response,
    next: NextFunction,
  ) => {
    try {
      const rawToken = req.cookies?.[opts.cookieName] as string | undefined;
      if (!rawToken) {
        res.status(401).json({ error: "Authentication required" });
        return;
      }

      const session = await opts.authStore.findSessionByTokenHash(
        sha256(rawToken),
      );
      if (
        !session ||
        session.expiresAt.getTime() <= Date.now() ||
        !session.user.isActive
      ) {
        res.status(401).json({ error: "Session invalid or expired" });
        return;
      }

      req.authUser = {
        id: session.user.id,
        email: session.user.email,
        role: session.user.role,
      };
      req.sessionId = session.id;
      req.csrfToken = session.csrfToken;
      next();
    } catch (err) {
      next(err);
    }
  };

  const requireOwner = (req: Request, res: Response, next: NextFunction) => {
    if (!req.authUser) {
      res.status(401).json({ error: "Authentication required" });
      return;
    }
    if (req.authUser.role !== "OWNER") {
      res.status(403).json({ error: "Owner permission required" });
      return;
    }
    next();
  };

  const requireCsrf = (req: Request, res: Response, next: NextFunction) => {
    const headerToken = req.header("x-csrf-token");
    if (
      !req.csrfToken ||
      !headerToken ||
      !timingSafeEqualStr(req.csrfToken, headerToken)
    ) {
      res.status(403).json({ error: "Invalid CSRF token" });
      return;
    }
    next();
  };

  const requireWorkerToken = (
    req: Request,
    res: Response,
    next: NextFunction,
  ) => {
    const authHeader = req.header("authorization") ?? "";
    if (!authHeader.startsWith("Bearer ")) {
      res.status(401).json({ error: "Worker token required" });
      return;
    }
    const token = authHeader.slice("Bearer ".length).trim();
    if (!timingSafeEqualStr(token, opts.workerAuthToken)) {
      res.status(403).json({ error: "Invalid worker token" });
      return;
    }
    next();
  };

  return {
    requireAuth,
    requireOwner,
    requireCsrf,
    requireWorkerToken,
  };
}

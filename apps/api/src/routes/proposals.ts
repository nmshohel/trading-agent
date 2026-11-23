import crypto from "node:crypto";
import { Router, type RequestHandler } from "express";
import rateLimit from "express-rate-limit";
import { z } from "zod";
import type { ExecuteProposalCommand } from "@trading-agent/shared";

export const ApproveProposalSchema = z.object({
  idempotencyKey: z.string().trim().min(8).max(128),
  comment: z.string().trim().max(300).optional(),
});

export const RejectProposalSchema = z.object({
  idempotencyKey: z.string().trim().min(8).max(128).optional(),
  comment: z.string().trim().max(300).optional(),
});

export const ListProposalsQuerySchema = z.object({
  status: z.string().optional(),
  limit: z.coerce.number().int().min(1).max(100).default(50),
});

export interface ProposalRecord {
  id: string;
  dedupKey: string;
  brokerAccountId: string;
  accountLogin?: string;
  accountCurrency?: string;
  currencyScale?: number;
  strategyId: string;
  strategyName?: string;
  symbol: string;
  timeframe: string;
  direction: "BUY" | "SELL";
  entry: string;
  stopLoss: string;
  takeProfit: string;
  volume: string;
  riskAmount: string;
  riskPercent: string;
  rewardRisk: string;
  estimatedMargin: string | null;
  spreadAtProposal: string;
  rationale: string;
  aiStatus: "COMPLETED" | "VETOED" | "UNAVAILABLE" | "SKIPPED";
  aiModel: string | null;
  aiPromptVersion: string | null;
  aiLatencyMs: number | null;
  aiTokensUsed: number | null;
  aiRawResponse: Record<string, unknown> | null;
  status: string;
  rejectionReason: string | null;
  expiresAt: string;
  claimedAt: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface ProposalWorkflowRepository {
  listProposals(input: {
    status?: string;
    limit: number;
  }): Promise<ProposalRecord[]>;
  findProposalById(id: string): Promise<ProposalRecord | null>;
  isSystemBlockedForNewOrders(): Promise<{
    blocked: boolean;
    reason?: string;
  }>;
  hasApprovalIdempotencyKey(idempotencyKey: string): Promise<boolean>;
  approveProposalAtomic(input: {
    proposalId: string;
    userId: string;
    idempotencyKey: string;
    comment?: string;
    now: Date;
  }): Promise<
    | { ok: true; proposal: ProposalRecord; approvalId: string }
    | { ok: false; code: number; error: string }
  >;
  rejectProposal(input: {
    proposalId: string;
    userId: string;
    idempotencyKey: string;
    comment?: string;
    now: Date;
  }): Promise<ProposalRecord | null>;
  cancelProposal(proposalId: string): Promise<ProposalRecord | null>;
  enqueueCommand(cmd: ExecuteProposalCommand): Promise<string>;
  writeAuditLog(input: {
    actorId?: string;
    action: string;
    entityType: string;
    entityId?: string;
    metadata: Record<string, unknown>;
    ip?: string;
  }): Promise<void>;
  publishSseEvent(event: {
    id: string;
    type: "proposal.created" | "proposal.updated" | "proposal.expired";
    timestamp: string;
    data: Record<string, unknown>;
  }): Promise<void>;
}

function withUsdEquivalent(p: ProposalRecord) {
  const scale = p.currencyScale && p.currencyScale > 0 ? p.currencyScale : 1;
  const riskNum = Number(p.riskAmount);
  const marginNum = p.estimatedMargin ? Number(p.estimatedMargin) : null;
  return {
    ...p,
    usdEquivalent: {
      riskAmount: Number.isFinite(riskNum)
        ? (riskNum / scale).toFixed(2)
        : "0.00",
      estimatedMargin:
        marginNum !== null && Number.isFinite(marginNum)
          ? (marginNum / scale).toFixed(2)
          : null,
    },
  };
}

export function createProposalsRouter(opts: {
  repo: ProposalWorkflowRepository;
  requireAuth: RequestHandler;
  requireOwner: RequestHandler;
  requireCsrf: RequestHandler;
}) {
  const router = Router();

  const actionLimiter = rateLimit({
    windowMs: 60 * 1000,
    max: 30,
    standardHeaders: true,
    legacyHeaders: false,
    message: { error: "Too many proposal actions; slow down" },
  });

  router.get(
    "/api/v1/proposals",
    opts.requireAuth,
    async (req, res, next) => {
      try {
        const query = ListProposalsQuerySchema.parse(req.query);
        const rows = await opts.repo.listProposals(query);
        res.status(200).json({ proposals: rows.map(withUsdEquivalent) });
      } catch (err) {
        next(err);
      }
    },
  );

  router.get(
    "/api/v1/proposals/:id",
    opts.requireAuth,
    async (req, res, next) => {
      try {
        const id = typeof req.params.id === "string" ? req.params.id : undefined;
        if (!id) {
          res.status(400).json({ error: "Proposal ID is required" });
          return;
        }
        const proposal = await opts.repo.findProposalById(id);
        if (!proposal) {
          res.status(404).json({ error: "Proposal not found" });
          return;
        }
        res.status(200).json({ proposal: withUsdEquivalent(proposal) });
      } catch (err) {
        next(err);
      }
    },
  );

  router.post(
    "/api/v1/proposals/:id/approve",
    actionLimiter,
    opts.requireAuth,
    opts.requireOwner,
    opts.requireCsrf,
    async (req, res, next) => {
      try {
        const id = typeof req.params.id === "string" ? req.params.id : undefined;
        if (!id) {
          res.status(400).json({ error: "Proposal ID is required" });
          return;
        }
        const parsed = ApproveProposalSchema.parse(req.body);

        const blocked = await opts.repo.isSystemBlockedForNewOrders();
        if (blocked.blocked) {
          res.status(409).json({
            error: blocked.reason ?? "Trading is currently blocked",
          });
          return;
        }

        const dupKey = await opts.repo.hasApprovalIdempotencyKey(
          parsed.idempotencyKey,
        );
        if (dupKey) {
          res.status(409).json({
            error: "Duplicate approval request (idempotencyKey already used)",
          });
          return;
        }

        const now = new Date();
        const result = await opts.repo.approveProposalAtomic({
          proposalId: id,
          userId: req.authUser!.id,
          idempotencyKey: parsed.idempotencyKey,
          comment: parsed.comment,
          now,
        });

        if (!result.ok) {
          res.status(result.code).json({ error: result.error });
          return;
        }

        const command: ExecuteProposalCommand = {
          type: "EXECUTE_PROPOSAL",
          commandId: crypto.randomUUID(),
          idempotencyKey: parsed.idempotencyKey,
          createdAt: now.toISOString(),
          payload: {
            proposalId: result.proposal.id,
            approvalId: result.approvalId,
            accountLogin: result.proposal.accountLogin ?? "unknown",
            symbol: result.proposal.symbol,
          },
        };
        const streamEntryId = await opts.repo.enqueueCommand(command);

        await opts.repo.writeAuditLog({
          actorId: req.authUser?.id,
          action: "PROPOSAL_APPROVED",
          entityType: "TradeProposal",
          entityId: result.proposal.id,
          metadata: {
            approvalId: result.approvalId,
            idempotencyKey: parsed.idempotencyKey,
            commandId: command.commandId,
            streamEntryId,
            symbol: result.proposal.symbol,
            direction: result.proposal.direction,
            volume: result.proposal.volume,
          },
          ip: req.ip,
        });

        await opts.repo.publishSseEvent({
          id: crypto.randomUUID(),
          type: "proposal.updated",
          timestamp: new Date().toISOString(),
          data: {
            proposalId: result.proposal.id,
            status: result.proposal.status,
            commandId: command.commandId,
          },
        });

        res.status(200).json({
          proposal: withUsdEquivalent(result.proposal),
          approvalId: result.approvalId,
          commandId: command.commandId,
        });
      } catch (err) {
        next(err);
      }
    },
  );

  router.post(
    "/api/v1/proposals/:id/reject",
    actionLimiter,
    opts.requireAuth,
    opts.requireOwner,
    opts.requireCsrf,
    async (req, res, next) => {
      try {
        const id = typeof req.params.id === "string" ? req.params.id : undefined;
        if (!id) {
          res.status(400).json({ error: "Proposal ID is required" });
          return;
        }
        const parsed = RejectProposalSchema.parse(req.body ?? {});
        const idempotencyKey = parsed.idempotencyKey ?? crypto.randomUUID();
        const updated = await opts.repo.rejectProposal({
          proposalId: id,
          userId: req.authUser!.id,
          idempotencyKey,
          comment: parsed.comment,
          now: new Date(),
        });
        if (!updated) {
          res.status(409).json({
            error: "Proposal not found or not in PENDING_APPROVAL state",
          });
          return;
        }

        await opts.repo.writeAuditLog({
          actorId: req.authUser?.id,
          action: "PROPOSAL_REJECTED",
          entityType: "TradeProposal",
          entityId: updated.id,
          metadata: { comment: parsed.comment ?? null },
          ip: req.ip,
        });

        await opts.repo.publishSseEvent({
          id: crypto.randomUUID(),
          type: "proposal.updated",
          timestamp: new Date().toISOString(),
          data: { proposalId: updated.id, status: updated.status },
        });

        res.status(200).json({ proposal: withUsdEquivalent(updated) });
      } catch (err) {
        next(err);
      }
    },
  );

  router.post(
    "/api/v1/proposals/:id/cancel",
    actionLimiter,
    opts.requireAuth,
    opts.requireOwner,
    opts.requireCsrf,
    async (req, res, next) => {
      try {
        const id = typeof req.params.id === "string" ? req.params.id : undefined;
        if (!id) {
          res.status(400).json({ error: "Proposal ID is required" });
          return;
        }
        const updated = await opts.repo.cancelProposal(id);
        if (!updated) {
          res.status(409).json({
            error: "Proposal not found or cannot be cancelled",
          });
          return;
        }

        await opts.repo.writeAuditLog({
          actorId: req.authUser?.id,
          action: "PROPOSAL_CANCELLED",
          entityType: "TradeProposal",
          entityId: updated.id,
          metadata: {},
          ip: req.ip,
        });

        await opts.repo.publishSseEvent({
          id: crypto.randomUUID(),
          type: "proposal.updated",
          timestamp: new Date().toISOString(),
          data: { proposalId: updated.id, status: updated.status },
        });

        res.status(200).json({ proposal: withUsdEquivalent(updated) });
      } catch (err) {
        next(err);
      }
    },
  );

  return router;
}

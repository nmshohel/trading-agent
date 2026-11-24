import crypto from "node:crypto";
import express from "express";
import helmet from "helmet";
import cors from "cors";
import cookieParser from "cookie-parser";
import { pinoHttp } from "pino-http";
import type { Prisma } from "@prisma/client";
import { REDIS_KEYS, SseEnvelopeSchema } from "@trading-agent/shared";
import { env, type AppEnv } from "./config/env.js";
import { logger } from "./lib/logger.js";
import { prisma } from "./lib/prisma.js";
import { createRedisClient, redis } from "./lib/redis.js";
import { SseHub } from "./lib/sseHub.js";
import {
  createAuthMiddleware,
  type AuthStore,
} from "./middleware/auth.js";
import { errorHandler } from "./middleware/errorHandler.js";
import {
  createHealthRouter,
  type HealthChecker,
} from "./routes/health.js";
import {
  createAuthRouter,
  type AuthRepository,
} from "./routes/auth.js";
import {
  createInternalRouter,
  HeartbeatPayloadSchema,
  type HeartbeatPayload,
  type InternalWorkerRepository,
  type MarketSnapshotPayload,
} from "./routes/internal.js";
import {
  createAccountRouter,
  type AccountQueryRepository,
} from "./routes/account.js";
import {
  createMarketRouter,
  type LatestQuoteRecord,
  type MarketQueryRepository,
} from "./routes/market.js";
import { createStreamRouter } from "./routes/stream.js";
import {
  createStrategiesRouter,
  type StrategyRepository,
} from "./routes/strategies.js";
import {
  createBlackoutsRouter,
  type BlackoutRepository,
} from "./routes/blackouts.js";
import {
  createSettingsRouter,
  type TradingSettingsRepository,
} from "./routes/settings.js";
import {
  createRiskRouter,
  type RiskResetRepository,
} from "./routes/risk.js";
import {
  createProposalsRouter,
  type ProposalRecord,
  type ProposalWorkflowRepository,
} from "./routes/proposals.js";
import {
  createSystemControlRouter,
  type SystemControlRepository,
} from "./routes/system.js";
import {
  createPositionsRouter,
  type PositionRecord,
  type PositionsAndOrdersRepository,
} from "./routes/positions.js";
import {
  createDashboardRouter,
  type DashboardAndReportsRepository,
} from "./routes/dashboard.js";

export interface AppDependencies {
  config?: AppEnv;
  authStore?: AuthStore;
  authRepo?: AuthRepository;
  healthChecker?: HealthChecker;
  internalRepo?: InternalWorkerRepository;
  accountRepo?: AccountQueryRepository;
  marketRepo?: MarketQueryRepository;
  strategyRepo?: StrategyRepository;
  blackoutRepo?: BlackoutRepository;
  settingsRepo?: TradingSettingsRepository;
  riskRepo?: RiskResetRepository;
  proposalRepo?: ProposalWorkflowRepository;
  systemControlRepo?: SystemControlRepository;
  positionsRepo?: PositionsAndOrdersRepository;
  dashboardRepo?: DashboardAndReportsRepository;
  sseHub?: SseHub;
}

const LATEST_QUOTE_REDIS_KEY = "market:latest_quote";
let inMemoryHeartbeat: HeartbeatPayload | null = null;
let inMemoryLatestQuote: LatestQuoteRecord | null = null;

function createPrismaAuthStore(): AuthStore {
  return {
    async findSessionByTokenHash(tokenHash) {
      const session = await prisma.session.findUnique({
        where: { tokenHash },
        include: {
          user: {
            select: { id: true, email: true, role: true, isActive: true },
          },
        },
      });
      if (!session) return null;
      return {
        id: session.id,
        userId: session.userId,
        csrfToken: session.csrfToken,
        expiresAt: session.expiresAt,
        user: session.user,
      };
    },
  };
}

function createPrismaAuthRepo(): AuthRepository {
  return {
    findUserByEmail(email) {
      return prisma.user.findUnique({
        where: { email },
        select: {
          id: true,
          email: true,
          passwordHash: true,
          role: true,
          isActive: true,
        },
      });
    },
    createSession(input) {
      return prisma.session.create({
        data: input,
        select: { id: true },
      });
    },
    async deleteSession(sessionId) {
      await prisma.session.deleteMany({ where: { id: sessionId } });
    },
    async writeAuditLog(input) {
      await prisma.auditLog.create({
        data: {
          actorId: input.actorId,
          action: input.action,
          entityType: input.entityType,
          entityId: input.entityId,
          metadata: input.metadata as Prisma.InputJsonValue,
          ip: input.ip,
        },
      });
    },
  };
}

async function ensureRedisConnected() {
  if (redis.status === "wait") {
    await redis.connect();
  }
}

function createDefaultHealthChecker(): HealthChecker {
  return {
    async checkDatabase() {
      await prisma.$queryRaw`SELECT 1`;
      return true;
    },
    async checkRedis() {
      await ensureRedisConnected();
      const pong = await redis.ping();
      return pong === "PONG";
    },
    async getWorkerHeartbeat(): Promise<HeartbeatPayload | null> {
      try {
        await ensureRedisConnected();
        const raw = await redis.get(REDIS_KEYS.WORKER_HEARTBEAT);
        if (raw) return HeartbeatPayloadSchema.parse(JSON.parse(raw));
      } catch {
        // Fallback to in-memory heartbeat when Redis is offline
      }
      return inMemoryHeartbeat;
    },
  };
}

function utcStartOfDay(now = new Date()): Date {
  return new Date(
    Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()),
  );
}

async function countTodayOpenTrades(
  brokerAccountId?: string,
  now = new Date(),
): Promise<number> {
  const start = utcStartOfDay(now);
  const [ordersCount, inFlightProposalsCount] = await Promise.all([
    prisma.tradeOrder.count({
      where: {
        ...(brokerAccountId ? { brokerAccountId } : {}),
        actionType: "OPEN",
        status: { in: ["REQUESTED", "ACCEPTED", "PARTIALLY_FILLED", "FILLED"] },
        createdAt: { gte: start },
      },
    }),
    prisma.tradeProposal.count({
      where: {
        ...(brokerAccountId ? { brokerAccountId } : {}),
        status: { in: ["APPROVED", "EXECUTING"] },
        createdAt: { gte: start },
      },
    }),
  ]);
  return ordersCount + inFlightProposalsCount;
}

function createPrismaInternalRepo(sseHub: SseHub): InternalWorkerRepository {
  return {
    async getRuntimeState() {
      const now = new Date();
      const [settings, strategies, blackouts, account] = await Promise.all([
        prisma.tradingSettings.findFirst({ orderBy: { updatedAt: "desc" } }),
        prisma.strategy.findMany({ where: { isActive: true } }),
        prisma.blackoutWindow.findMany({
          where: { isActive: true, endsAt: { gt: now } },
          orderBy: { startsAt: "asc" },
        }),
        prisma.brokerAccount.findFirst({
          where: { isEnabled: true },
          orderBy: { updatedAt: "desc" },
        }),
      ]);

      let dailySnapshot = null;
      let weeklySnapshot = null;
      let dailyTradesCount = 0;
      if (account) {
        [dailySnapshot, weeklySnapshot, dailyTradesCount] = await Promise.all([
          prisma.dailyRiskSnapshot.findFirst({
            where: { brokerAccountId: account.id },
            orderBy: { date: "desc" },
          }),
          prisma.weeklyRiskSnapshot.findFirst({
            where: { brokerAccountId: account.id },
            orderBy: { weekStart: "desc" },
          }),
          countTodayOpenTrades(account.id, now),
        ]);
      }

      let redisKillSwitch = false;
      try {
        await ensureRedisConnected();
        redisKillSwitch = (await redis.get(REDIS_KEYS.KILL_SWITCH_FLAG)) === "1";
      } catch {
        redisKillSwitch = false;
      }

      return {
        fetchedAt: now.toISOString(),
        killSwitchActive:
          Boolean(settings?.killSwitchActive) || redisKillSwitch,
        isPaused: Boolean(settings?.isPaused),
        dailyTradesCount,
        maxDailyTrades: settings?.maxDailyTrades ?? 5,
        settings,
        strategies,
        blackouts,
        account,
        dailySnapshot,
        weeklySnapshot,
      };
    },

    async findBrokerAccount(login, server) {
      return prisma.brokerAccount.findUnique({
        where: { login_server: { login, server } },
        select: { id: true, mode: true, liveUnlockedAt: true },
      });
    },

    async upsertBrokerAccount(payload) {
      const now = new Date();
      const account = await prisma.brokerAccount.upsert({
        where: {
          login_server: {
            login: payload.login,
            server: payload.server,
          },
        },
        update: {
          broker: payload.broker,
          platform: payload.platform,
          accountCurrency: payload.accountCurrency,
          currencyScale: payload.currencyScale,
          accountType: payload.accountType,
          marginMode: payload.marginMode,
          magicNumber: payload.magicNumber,
          balance: payload.balance,
          equity: payload.equity,
          freeMargin: payload.freeMargin,
          symbolSpecs: payload.symbolSpecs as Prisma.InputJsonValue,
          lastSyncedAt: now,
        },
        create: {
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
          symbolSpecs: payload.symbolSpecs as Prisma.InputJsonValue,
          lastSyncedAt: now,
        },
      });

      return {
        ...account,
        balance: account.balance.toString(),
        equity: account.equity.toString(),
        freeMargin: account.freeMargin.toString(),
        lastSyncedAt: account.lastSyncedAt?.toISOString() ?? null,
      };
    },

    async upsertMarketSnapshot(payload: MarketSnapshotPayload) {
      const candleDate = new Date(payload.candleTime);
      const saved = await prisma.marketSnapshot.upsert({
        where: {
          symbol_timeframe_candleTime: {
            symbol: payload.symbol,
            timeframe: payload.timeframe,
            candleTime: candleDate,
          },
        },
        update: {
          open: payload.open,
          high: payload.high,
          low: payload.low,
          close: payload.close,
          volume: payload.volume,
          bid: payload.bid,
          ask: payload.ask,
          spread: payload.spread,
          indicators: payload.indicators as Prisma.InputJsonValue,
        },
        create: {
          symbol: payload.symbol,
          timeframe: payload.timeframe,
          candleTime: candleDate,
          open: payload.open,
          high: payload.high,
          low: payload.low,
          close: payload.close,
          volume: payload.volume,
          bid: payload.bid,
          ask: payload.ask,
          spread: payload.spread,
          indicators: payload.indicators as Prisma.InputJsonValue,
        },
        select: { id: true, candleTime: true },
      });

      const latest: LatestQuoteRecord = {
        symbol: payload.symbol,
        bid: payload.bid,
        ask: payload.ask,
        spread: payload.spread,
        quoteTime: new Date().toISOString(),
        indicators: payload.indicators,
      };
      inMemoryLatestQuote = latest;
      try {
        await ensureRedisConnected();
        await redis.set(LATEST_QUOTE_REDIS_KEY, JSON.stringify(latest), "EX", 120);
      } catch {
        // Non-fatal cache write
      }

      return { id: saved.id, candleTime: saved.candleTime.toISOString() };
    },

    async syncRiskSnapshots(payload) {
      const now = new Date();
      const dayDate = new Date(`${payload.date}T00:00:00.000Z`);
      const weekDate = new Date(`${payload.weekStart}T00:00:00.000Z`);

      const [daily, weekly] = await Promise.all([
        prisma.dailyRiskSnapshot.upsert({
          where: {
            brokerAccountId_date: {
              brokerAccountId: payload.brokerAccountId,
              date: dayDate,
            },
          },
          update: {
            currentEquity: payload.currentEquity,
            realizedPnl: payload.realizedDailyPnl,
            unrealizedPnl: payload.unrealizedPnl,
            lossLimit: payload.dailyLossLimit,
            ...(payload.dailyTripped ? { isTripped: true, trippedAt: now } : {}),
          },
          create: {
            brokerAccountId: payload.brokerAccountId,
            date: dayDate,
            startingEquity: payload.startingDailyEquity,
            currentEquity: payload.currentEquity,
            realizedPnl: payload.realizedDailyPnl,
            unrealizedPnl: payload.unrealizedPnl,
            lossLimit: payload.dailyLossLimit,
            isTripped: payload.dailyTripped,
            trippedAt: payload.dailyTripped ? now : null,
          },
          select: { id: true },
        }),
        prisma.weeklyRiskSnapshot.upsert({
          where: {
            brokerAccountId_weekStart: {
              brokerAccountId: payload.brokerAccountId,
              weekStart: weekDate,
            },
          },
          update: {
            currentEquity: payload.currentEquity,
            realizedPnl: payload.realizedWeeklyPnl,
            unrealizedPnl: payload.unrealizedPnl,
            lossLimit: payload.weeklyLossLimit,
            ...(payload.weeklyTripped ? { isTripped: true, trippedAt: now } : {}),
          },
          create: {
            brokerAccountId: payload.brokerAccountId,
            weekStart: weekDate,
            startingEquity: payload.startingWeeklyEquity,
            currentEquity: payload.currentEquity,
            realizedPnl: payload.realizedWeeklyPnl,
            unrealizedPnl: payload.unrealizedPnl,
            lossLimit: payload.weeklyLossLimit,
            isTripped: payload.weeklyTripped,
            trippedAt: payload.weeklyTripped ? now : null,
          },
          select: { id: true },
        }),
      ]);

      return { dailyId: daily.id, weeklyId: weekly.id };
    },

    async createProposal(payload) {
      const existing = await prisma.tradeProposal.findUnique({
        where: { dedupKey: payload.dedupKey },
        select: { id: true },
      });
      if (existing) {
        return {
          ok: false,
          code: 409,
          error: "Proposal with this dedupKey already exists",
        };
      }

      if (payload.status === "PENDING_APPROVAL") {
        const settings = await prisma.tradingSettings.findFirst({
          orderBy: { updatedAt: "desc" },
        });
        const maxDailyTrades = settings?.maxDailyTrades ?? 5;
        const todayTrades = await countTodayOpenTrades(payload.brokerAccountId);
        if (todayTrades >= maxDailyTrades) {
          return {
            ok: false,
            code: 409,
            error: `Daily max trades limit (${todayTrades}/${maxDailyTrades}) reached for today (UTC)`,
          };
        }
        const maxPending = settings?.maxPendingProposals ?? 3;
        const pendingCount = await prisma.tradeProposal.count({
          where: { status: "PENDING_APPROVAL", expiresAt: { gt: new Date() } },
        });
        if (pendingCount >= maxPending) {
          return {
            ok: false,
            code: 409,
            error: `Maximum pending proposals (${maxPending}) reached`,
          };
        }
      }

      const created = await prisma.tradeProposal.create({
        data: {
          dedupKey: payload.dedupKey,
          brokerAccountId: payload.brokerAccountId,
          strategyId: payload.strategyId,
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
          aiRawResponse: (payload.aiRawResponse ??
            undefined) as Prisma.InputJsonValue | undefined,
          status: payload.status,
          rejectionReason: payload.rejectionReason ?? null,
          expiresAt: new Date(payload.expiresAt),
        },
      });

      let finalStatus: string = created.status;
      let autoApproved = false;

      if (payload.status === "PENDING_APPROVAL") {
        const [settingsRow, brokerAccount, openPosCount, ownerUser] =
          await Promise.all([
            prisma.tradingSettings.findFirst({ orderBy: { updatedAt: "desc" } }),
            prisma.brokerAccount.findUnique({
              where: { id: payload.brokerAccountId },
              select: { id: true, login: true, mode: true, liveUnlockedAt: true },
            }),
            prisma.position.count({
              where: {
                brokerAccountId: payload.brokerAccountId,
                status: { in: ["OPEN", "CLOSING"] },
              },
            }),
            prisma.user.findFirst({
              where: { role: "OWNER", isActive: true },
              select: { id: true },
            }),
          ]);

        let redisKillSwitch = false;
        try {
          await ensureRedisConnected();
          redisKillSwitch =
            (await redis.get(REDIS_KEYS.KILL_SWITCH_FLAG)) === "1";
        } catch {
          redisKillSwitch = false;
        }

        const isStrictDemoAccount =
          brokerAccount !== null &&
          brokerAccount.mode === "DEMO" &&
          brokerAccount.liveUnlockedAt === null;

        const eligibleForDemoAutoExecute =
          Boolean(settingsRow?.demoAutoExecute) &&
          !settingsRow?.isPaused &&
          !settingsRow?.killSwitchActive &&
          !redisKillSwitch &&
          isStrictDemoAccount &&
          openPosCount < (settingsRow?.maxPositions ?? 1) &&
          ownerUser !== null;

        if (eligibleForDemoAutoExecute && brokerAccount && ownerUser) {
          const now = new Date();
          const autoKey = `demo-auto:${created.id}`;
          const approvalResult = await prisma.$transaction(async (tx) => {
            const upd = await tx.tradeProposal.updateMany({
              where: {
                id: created.id,
                status: "PENDING_APPROVAL",
                expiresAt: { gt: now },
              },
              data: { status: "APPROVED" },
            });
            if (upd.count === 0) return null;

            const approval = await tx.tradeApproval.create({
              data: {
                proposalId: created.id,
                userId: ownerUser.id,
                decision: "APPROVE",
                approvedAt: now,
                idempotencyKey: autoKey,
                comment: "DEMO_AUTO_EXECUTE",
              },
              select: { id: true },
            });

            await tx.auditLog.create({
              data: {
                actorId: ownerUser.id,
                action: "PROPOSAL_DEMO_AUTO_APPROVED",
                entityType: "TradeProposal",
                entityId: created.id,
                metadata: {
                  symbol: created.symbol,
                  direction: created.direction,
                  volume: created.volume.toString(),
                  accountMode: "DEMO",
                  reason: "DEMO_AUTO_EXECUTE",
                },
              },
            });

            return approval;
          });

          if (approvalResult) {
            try {
              await ensureRedisConnected();
              await redis.xadd(
                REDIS_KEYS.COMMAND_STREAM,
                "*",
                "payload",
                JSON.stringify({
                  type: "EXECUTE_PROPOSAL",
                  commandId: crypto.randomUUID(),
                  idempotencyKey: autoKey,
                  createdAt: now.toISOString(),
                  payload: {
                    proposalId: created.id,
                    approvalId: approvalResult.id,
                    accountLogin: brokerAccount.login,
                    symbol: created.symbol,
                    autoApproved: true,
                  },
                }),
              );
              finalStatus = "APPROVED";
              autoApproved = true;
            } catch (err) {
              logger.warn(
                { err, proposalId: created.id },
                "Demo auto-execute Redis enqueue failed; reverting proposal to PENDING_APPROVAL",
              );
              await prisma.tradeProposal.updateMany({
                where: { id: created.id, status: "APPROVED" },
                data: { status: "PENDING_APPROVAL" },
              });
            }
          }
        }
      }

      return {
        ok: true,
        proposal: {
          id: created.id,
          dedupKey: created.dedupKey,
          status: finalStatus,
          expiresAt: created.expiresAt.toISOString(),
          autoApproved,
        },
      };
    },

    async expirePendingProposals(now) {
      const overdue = await prisma.tradeProposal.findMany({
        where: {
          status: "PENDING_APPROVAL",
          expiresAt: { lte: now },
        },
        select: { id: true },
      });
      if (overdue.length === 0) return { expiredIds: [] };

      const ids = overdue.map((o) => o.id);
      await prisma.tradeProposal.updateMany({
        where: { id: { in: ids }, status: "PENDING_APPROVAL" },
        data: { status: "EXPIRED" },
      });
      return { expiredIds: ids };
    },

    async claimProposalAtomic(proposalId, now) {
      const updated = await prisma.tradeProposal.updateMany({
        where: {
          id: proposalId,
          status: "APPROVED",
          expiresAt: { gt: now },
        },
        data: {
          status: "EXECUTING",
          claimedAt: now,
        },
      });
      if (updated.count === 0) {
        return {
          ok: false,
          code: 409,
          error:
            "Proposal cannot be claimed (not APPROVED, expired, or already claimed)",
        };
      }
      const p = await prisma.tradeProposal.findUnique({
        where: { id: proposalId },
        include: {
          approvals: {
            where: { decision: "APPROVE" },
            orderBy: { createdAt: "desc" },
            take: 1,
            select: { comment: true },
          },
        },
      });
      return {
        ok: true,
        proposal: p
          ? {
              id: p.id,
              status: p.status,
              symbol: p.symbol,
              direction: p.direction,
              entry: p.entry.toString(),
              stopLoss: p.stopLoss.toString(),
              takeProfit: p.takeProfit.toString(),
              volume: p.volume.toString(),
              claimedAt: p.claimedAt?.toISOString() ?? null,
              autoApproved: p.approvals[0]?.comment === "DEMO_AUTO_EXECUTE",
            }
          : { id: proposalId, status: "EXECUTING" },
      };
    },

    async getStuckReconciliation(thresholdSeconds) {
      const cutoff = new Date(Date.now() - thresholdSeconds * 1000);
      const [stuckProposals, stuckPositions] = await Promise.all([
        prisma.tradeProposal.findMany({
          where: {
            status: "EXECUTING",
            claimedAt: { lte: cutoff },
          },
          select: {
            id: true,
            brokerAccountId: true,
            symbol: true,
            direction: true,
            volume: true,
            claimedAt: true,
          },
        }),
        prisma.position.findMany({
          where: {
            status: "CLOSING",
            closeRequestedAt: { lte: cutoff },
          },
          select: {
            id: true,
            brokerAccountId: true,
            brokerPositionId: true,
            symbol: true,
            side: true,
            volume: true,
            closeRequestedAt: true,
          },
        }),
      ]);

      return {
        stuckProposals: stuckProposals.map((p) => ({
          ...p,
          volume: p.volume.toString(),
          claimedAt: p.claimedAt?.toISOString() ?? null,
        })),
        stuckPositions: stuckPositions.map((p) => ({
          ...p,
          volume: p.volume.toString(),
          closeRequestedAt: p.closeRequestedAt?.toISOString() ?? null,
        })),
      };
    },

    async recordOrderResult(payload) {
      return prisma.$transaction(async (tx) => {
        let positionId = payload.positionId ?? null;

        if (
          payload.actionType === "OPEN" &&
          payload.orderStatus === "FILLED" &&
          payload.brokerPositionId
        ) {
          const pos = await tx.position.upsert({
            where: { brokerPositionId: payload.brokerPositionId },
            update: {
              currentPrice: payload.entryPrice ?? payload.requestedPrice ?? "0",
              stopLoss: payload.stopLoss ?? null,
              takeProfit: payload.takeProfit ?? null,
              status: "OPEN",
            },
            create: {
              brokerAccountId: payload.brokerAccountId,
              brokerPositionId: payload.brokerPositionId,
              symbol: payload.symbol,
              side: payload.side,
              volume: payload.volume,
              openPrice: payload.entryPrice ?? payload.requestedPrice ?? "0",
              currentPrice: payload.entryPrice ?? payload.requestedPrice ?? "0",
              stopLoss: payload.stopLoss ?? null,
              takeProfit: payload.takeProfit ?? null,
              status: "OPEN",
              openedAt: new Date(),
            },
            select: { id: true },
          });
          positionId = pos.id;
        }

        if (positionId && payload.positionStatus) {
          await tx.position.update({
            where: { id: positionId },
            data: {
              status: payload.positionStatus,
              ...(payload.closePrice ? { closePrice: payload.closePrice } : {}),
              ...(payload.realizedProfit
                ? { profit: payload.realizedProfit }
                : {}),
              ...(payload.commission ? { commission: payload.commission } : {}),
              ...(payload.swap ? { swap: payload.swap } : {}),
              ...(payload.positionStatus === "CLOSED"
                ? { closedAt: new Date() }
                : {}),
              ...(payload.positionStatus === "OPEN"
                ? { closeRequestedAt: null, closeIdempotencyKey: null }
                : {}),
            },
          });
        }

        if (payload.proposalId && payload.proposalStatus) {
          await tx.tradeProposal.update({
            where: { id: payload.proposalId },
            data: {
              status: payload.proposalStatus,
              ...(payload.failureReason
                ? { rejectionReason: payload.failureReason }
                : {}),
            },
          });
        }

        const order = await tx.tradeOrder.upsert({
          where: { idempotencyKey: payload.idempotencyKey },
          update: {
            positionId,
            brokerOrderId: payload.brokerOrderId ?? null,
            brokerDealId: payload.brokerDealId ?? null,
            entryPrice: payload.entryPrice ?? null,
            retcode: payload.retcode ?? null,
            brokerComment: payload.brokerComment ?? null,
            status: payload.orderStatus,
          },
          create: {
            proposalId: payload.proposalId ?? null,
            positionId,
            brokerAccountId: payload.brokerAccountId,
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
            idempotencyKey: payload.idempotencyKey,
          },
          select: { id: true },
        });

        await tx.tradeEvent.create({
          data: {
            tradeOrderId: order.id,
            positionId,
            eventType: `${payload.actionType}_${payload.orderStatus}`,
            payload: payload.eventPayload as Prisma.InputJsonValue,
          },
        });

        return { orderId: order.id, positionId };
      });
    },

    async syncPositions(payload) {
      for (const item of payload.positions) {
        await prisma.position.upsert({
          where: { brokerPositionId: item.brokerPositionId },
          update: {
            currentPrice: item.currentPrice,
            closePrice: item.closePrice ?? undefined,
            stopLoss: item.stopLoss ?? null,
            takeProfit: item.takeProfit ?? null,
            profit: item.profit,
            commission: item.commission,
            swap: item.swap,
            magicNumber: item.magicNumber ?? null,
            isBotManaged: item.isBotManaged,
            ...(item.status === "CLOSED"
              ? {
                  status: "CLOSED",
                  closedAt: item.closedAt ? new Date(item.closedAt) : new Date(),
                }
              : {}),
          },
          create: {
            brokerAccountId: payload.brokerAccountId,
            brokerPositionId: item.brokerPositionId,
            symbol: item.symbol,
            side: item.side,
            volume: item.volume,
            openPrice: item.openPrice,
            currentPrice: item.currentPrice,
            closePrice: item.closePrice ?? null,
            stopLoss: item.stopLoss ?? null,
            takeProfit: item.takeProfit ?? null,
            profit: item.profit,
            commission: item.commission,
            swap: item.swap,
            magicNumber: item.magicNumber ?? null,
            isBotManaged: item.isBotManaged,
            status: item.status,
            openedAt: new Date(item.openedAt),
            closedAt: item.closedAt ? new Date(item.closedAt) : null,
          },
        });
      }
      return { syncedCount: payload.positions.length };
    },

    async createSystemEvent(payload) {
      return prisma.systemEvent.create({
        data: {
          service: payload.service,
          severity: payload.severity,
          message: payload.message,
          metadata: payload.metadata as Prisma.InputJsonValue,
        },
        select: { id: true, createdAt: true },
      });
    },

    async saveHeartbeat(payload) {
      inMemoryHeartbeat = payload;
      try {
        await ensureRedisConnected();
        await redis.set(
          REDIS_KEYS.WORKER_HEARTBEAT,
          JSON.stringify(payload),
          "EX",
          60,
        );
      } catch (err) {
        logger.warn({ err }, "Failed to persist worker heartbeat to Redis");
      }
    },

    async publishSseEvent(event) {
      const parsed = SseEnvelopeSchema.safeParse(event);
      try {
        await ensureRedisConnected();
        await redis.publish(
          REDIS_KEYS.SSE_PUBSUB_CHANNEL,
          JSON.stringify(event),
        );
      } catch (err) {
        logger.warn({ err }, "Failed to publish SSE event to Redis; broadcasting locally");
        if (parsed.success) {
          sseHub.broadcast(parsed.data);
        }
      }
    },
  };
}

function createPrismaAccountRepo(
  internalRepo: InternalWorkerRepository,
): AccountQueryRepository {
  const serialize = (account: {
    id: string;
    broker: string;
    platform: string;
    login: string;
    server: string;
    accountCurrency: string;
    currencyScale: number;
    accountType: "STANDARD" | "CENT";
    marginMode: string;
    magicNumber: number;
    mode: "DEMO" | "LIVE";
    balance: { toString(): string };
    equity: { toString(): string };
    freeMargin: { toString(): string };
    symbolSpecs: unknown;
    liveUnlockedAt: Date | null;
    liveUnlockedBy: string | null;
    isEnabled: boolean;
    lastSyncedAt: Date | null;
  }) => ({
    id: account.id,
    broker: account.broker,
    platform: account.platform,
    login: account.login,
    server: account.server,
    accountCurrency: account.accountCurrency,
    currencyScale: account.currencyScale,
    accountType: account.accountType,
    marginMode: account.marginMode,
    magicNumber: account.magicNumber,
    mode: account.mode,
    balance: account.balance.toString(),
    equity: account.equity.toString(),
    freeMargin: account.freeMargin.toString(),
    symbolSpecs: account.symbolSpecs,
    liveUnlockedAt: account.liveUnlockedAt?.toISOString() ?? null,
    liveUnlockedBy: account.liveUnlockedBy,
    isEnabled: account.isEnabled,
    lastSyncedAt: account.lastSyncedAt?.toISOString() ?? null,
  });

  return {
    async getActiveBrokerAccount() {
      const account = await prisma.brokerAccount.findFirst({
        where: { isEnabled: true },
        orderBy: { updatedAt: "desc" },
      });
      return account ? serialize(account) : null;
    },
    async getUserPasswordHash(userId) {
      const u = await prisma.user.findUnique({
        where: { id: userId },
        select: { passwordHash: true },
      });
      return u?.passwordHash ?? null;
    },
    async unlockLiveMode({ accountId, userId, unlockedAt }) {
      const updated = await prisma.$transaction(async (tx) => {
        // Hard safety lock: disable demoAutoExecute whenever LIVE mode is unlocked
        await tx.tradingSettings.updateMany({
          data: { demoAutoExecute: false },
        });
        return tx.brokerAccount.update({
          where: { id: accountId },
          data: {
            mode: "LIVE",
            liveUnlockedAt: unlockedAt,
            liveUnlockedBy: userId,
          },
        });
      });
      return serialize(updated);
    },
    async lockDemoMode(accountId) {
      const updated = await prisma.brokerAccount.update({
        where: { id: accountId },
        data: {
          mode: "DEMO",
          liveUnlockedAt: null,
          liveUnlockedBy: null,
        },
      });
      return serialize(updated);
    },
    async writeAuditLog(input) {
      await prisma.auditLog.create({
        data: {
          actorId: input.actorId,
          action: input.action,
          entityType: input.entityType,
          entityId: input.entityId,
          metadata: input.metadata as Prisma.InputJsonValue,
          ip: input.ip,
        },
      });
    },
    async publishSseEvent(event) {
      await internalRepo.publishSseEvent(event);
    },
  };
}

function createPrismaMarketRepo(): MarketQueryRepository {
  return {
    async getLatestQuote(symbol) {
      try {
        await ensureRedisConnected();
        const raw = await redis.get(LATEST_QUOTE_REDIS_KEY);
        if (raw) {
          const parsed = JSON.parse(raw) as LatestQuoteRecord;
          if (!symbol || parsed.symbol === symbol) {
            return parsed;
          }
        }
      } catch {
        // Fallback to in-memory or DB snapshot
      }
      if (inMemoryLatestQuote && (!symbol || inMemoryLatestQuote.symbol === symbol)) {
        return inMemoryLatestQuote;
      }

      const snap = await prisma.marketSnapshot.findFirst({
        where: symbol ? { symbol } : undefined,
        orderBy: { candleTime: "desc" },
      });
      if (!snap) return null;
      return {
        symbol: snap.symbol,
        bid: snap.bid.toString(),
        ask: snap.ask.toString(),
        spread: snap.spread.toString(),
        quoteTime: snap.createdAt.toISOString(),
        indicators: (snap.indicators ?? {}) as Record<string, unknown>,
      };
    },

    async getCandles({ symbol, timeframe, limit }) {
      const rows = await prisma.marketSnapshot.findMany({
        where: {
          timeframe,
          ...(symbol ? { symbol } : {}),
        },
        orderBy: { candleTime: "desc" },
        take: limit,
      });

      return rows.reverse().map((r) => ({
        symbol: r.symbol,
        timeframe: r.timeframe,
        candleTime: r.candleTime.toISOString(),
        open: r.open.toString(),
        high: r.high.toString(),
        low: r.low.toString(),
        close: r.close.toString(),
        volume: r.volume.toString(),
        bid: r.bid.toString(),
        ask: r.ask.toString(),
        spread: r.spread.toString(),
        indicators: (r.indicators ?? {}) as Record<string, unknown>,
      }));
    },
  };
}

export function startRedisSseBridge(sseHub: SseHub, redisUrl = env.REDIS_URL) {
  const subscriber = createRedisClient(redisUrl);

  subscriber.on("message", (_channel, message) => {
    try {
      const raw = JSON.parse(message) as unknown;
      const parsed = SseEnvelopeSchema.safeParse(raw);
      if (!parsed.success) return;

      if (parsed.data.type === "quote.updated") {
        const d = parsed.data.data;
        if (
          typeof d.symbol === "string" &&
          typeof d.bid === "string" &&
          typeof d.ask === "string" &&
          typeof d.spread === "string"
        ) {
          const latest: LatestQuoteRecord = {
            symbol: d.symbol,
            bid: d.bid,
            ask: d.ask,
            spread: d.spread,
            quoteTime:
              typeof d.quoteTime === "string"
                ? d.quoteTime
                : parsed.data.timestamp,
          };
          void redis
            .set(LATEST_QUOTE_REDIS_KEY, JSON.stringify(latest), "EX", 120)
            .catch(() => {});
        }
      }

      sseHub.broadcast(parsed.data);
    } catch (err) {
      logger.warn({ err }, "Failed to handle Redis Pub/Sub SSE message");
    }
  });

  void subscriber
    .connect()
    .then(() => subscriber.subscribe(REDIS_KEYS.SSE_PUBSUB_CHANNEL))
    .then(() => {
      logger.info(
        { channel: REDIS_KEYS.SSE_PUBSUB_CHANNEL },
        "Subscribed Redis -> SSE bridge",
      );
    })
    .catch((err) => {
      logger.warn({ err }, "Redis SSE bridge failed to connect at startup");
    });

  return subscriber;
}

function createPrismaStrategyRepo(
  internalRepo: InternalWorkerRepository,
): StrategyRepository {
  return {
    async listStrategies() {
      const rows = await prisma.strategy.findMany({
        orderBy: [{ name: "asc" }, { version: "desc" }],
      });
      return rows.map((r) => ({
        id: r.id,
        name: r.name,
        version: r.version,
        timeframe: r.timeframe,
        parameters: (r.parameters ?? {}) as Record<string, unknown>,
        isActive: r.isActive,
        updatedAt: r.updatedAt.toISOString(),
      }));
    },
    async findStrategyById(id) {
      const r = await prisma.strategy.findUnique({ where: { id } });
      if (!r) return null;
      return {
        id: r.id,
        name: r.name,
        version: r.version,
        timeframe: r.timeframe,
        parameters: (r.parameters ?? {}) as Record<string, unknown>,
        isActive: r.isActive,
        updatedAt: r.updatedAt.toISOString(),
      };
    },
    async updateStrategy(id, input) {
      const r = await prisma.strategy.update({
        where: { id },
        data: {
          ...(input.isActive !== undefined ? { isActive: input.isActive } : {}),
          ...(input.parameters !== undefined
            ? { parameters: input.parameters as Prisma.InputJsonValue }
            : {}),
        },
      });
      return {
        id: r.id,
        name: r.name,
        version: r.version,
        timeframe: r.timeframe,
        parameters: (r.parameters ?? {}) as Record<string, unknown>,
        isActive: r.isActive,
        updatedAt: r.updatedAt.toISOString(),
      };
    },
    async writeAuditLog(input) {
      await prisma.auditLog.create({
        data: {
          actorId: input.actorId,
          action: input.action,
          entityType: input.entityType,
          entityId: input.entityId,
          metadata: input.metadata as Prisma.InputJsonValue,
          ip: input.ip,
        },
      });
    },
    async notifySettingsUpdated(reason) {
      await internalRepo.publishSseEvent({
        id: crypto.randomUUID(),
        type: "settings.updated",
        timestamp: new Date().toISOString(),
        data: { reason },
      });
    },
  };
}

function createPrismaBlackoutRepo(
  internalRepo: InternalWorkerRepository,
): BlackoutRepository {
  return {
    async listBlackouts() {
      const rows = await prisma.blackoutWindow.findMany({
        orderBy: { startsAt: "asc" },
      });
      return rows.map((r) => ({
        id: r.id,
        title: r.title,
        reason: r.reason,
        startsAt: r.startsAt.toISOString(),
        endsAt: r.endsAt.toISOString(),
        isActive: r.isActive,
        createdById: r.createdById,
        createdAt: r.createdAt.toISOString(),
      }));
    },
    async createBlackout(input) {
      const r = await prisma.blackoutWindow.create({ data: input });
      return {
        id: r.id,
        title: r.title,
        reason: r.reason,
        startsAt: r.startsAt.toISOString(),
        endsAt: r.endsAt.toISOString(),
        isActive: r.isActive,
        createdById: r.createdById,
        createdAt: r.createdAt.toISOString(),
      };
    },
    async deleteBlackout(id) {
      const res = await prisma.blackoutWindow.deleteMany({ where: { id } });
      return res.count > 0;
    },
    async writeAuditLog(input) {
      await prisma.auditLog.create({
        data: {
          actorId: input.actorId,
          action: input.action,
          entityType: input.entityType,
          entityId: input.entityId,
          metadata: input.metadata as Prisma.InputJsonValue,
          ip: input.ip,
        },
      });
    },
    async notifySettingsUpdated(reason) {
      await internalRepo.publishSseEvent({
        id: crypto.randomUUID(),
        type: "settings.updated",
        timestamp: new Date().toISOString(),
        data: { reason },
      });
    },
  };
}

function createPrismaSettingsRepo(
  internalRepo: InternalWorkerRepository,
): TradingSettingsRepository {
  const serialize = (s: {
    id: string;
    symbol: string;
    riskPercent: { toString(): string };
    dailyLossLimit: { toString(): string };
    weeklyLossLimit: { toString(): string };
    maxPositions: number;
    maxDailyTrades: number;
    maxPendingProposals: number;
    minRewardRisk: { toString(): string };
    maxSpread: { toString(): string };
    maxDeviationPoints: number;
    commissionPerLot: { toString(): string };
    approvalExpirySeconds: number;
    postRejectionCooldownMinutes: number;
    rolloverBlackoutMinutes: number;
    sessionWindows: unknown;
    aiFallbackMode: "DETERMINISTIC_ONLY" | "SKIP";
    demoAutoExecute: boolean;
    isPaused: boolean;
    killSwitchActive: boolean;
    cooldownUntil: Date | null;
    updatedAt: Date;
  }) => ({
    id: s.id,
    symbol: s.symbol,
    riskPercent: s.riskPercent.toString(),
    dailyLossLimit: s.dailyLossLimit.toString(),
    weeklyLossLimit: s.weeklyLossLimit.toString(),
    maxPositions: s.maxPositions,
    maxDailyTrades: s.maxDailyTrades,
    maxPendingProposals: s.maxPendingProposals,
    minRewardRisk: s.minRewardRisk.toString(),
    maxSpread: s.maxSpread.toString(),
    maxDeviationPoints: s.maxDeviationPoints,
    commissionPerLot: s.commissionPerLot.toString(),
    approvalExpirySeconds: s.approvalExpirySeconds,
    postRejectionCooldownMinutes: s.postRejectionCooldownMinutes,
    rolloverBlackoutMinutes: s.rolloverBlackoutMinutes,
    sessionWindows: (s.sessionWindows ?? {}) as Record<string, unknown>,
    aiFallbackMode: s.aiFallbackMode,
    demoAutoExecute: s.demoAutoExecute,
    isPaused: s.isPaused,
    killSwitchActive: s.killSwitchActive,
    cooldownUntil: s.cooldownUntil?.toISOString() ?? null,
    updatedAt: s.updatedAt.toISOString(),
  });

  return {
    async getSettings() {
      const s = await prisma.tradingSettings.findFirst({
        orderBy: { updatedAt: "desc" },
      });
      return s ? serialize(s) : null;
    },
    async getActiveAccountMode() {
      const account = await prisma.brokerAccount.findFirst({
        where: { isEnabled: true },
        orderBy: { updatedAt: "desc" },
        select: { mode: true, liveUnlockedAt: true },
      });
      if (!account) return null;
      return {
        mode: account.mode,
        liveUnlockedAt: account.liveUnlockedAt?.toISOString() ?? null,
      };
    },
    async updateSettings(id, patch) {
      const { sessionWindows, ...rest } = patch;
      const updated = await prisma.tradingSettings.update({
        where: { id },
        data: {
          ...rest,
          ...(sessionWindows !== undefined
            ? { sessionWindows: sessionWindows as Prisma.InputJsonValue }
            : {}),
        },
      });
      return serialize(updated);
    },
    async writeAuditLog(input) {
      await prisma.auditLog.create({
        data: {
          actorId: input.actorId,
          action: input.action,
          entityType: input.entityType,
          entityId: input.entityId,
          metadata: input.metadata as Prisma.InputJsonValue,
          ip: input.ip,
        },
      });
    },
    async notifySettingsUpdated(reason) {
      await internalRepo.publishSseEvent({
        id: crypto.randomUUID(),
        type: "settings.updated",
        timestamp: new Date().toISOString(),
        data: { reason },
      });
    },
  };
}

function createPrismaRiskRepo(
  internalRepo: InternalWorkerRepository,
): RiskResetRepository {
  const serialize = (w: {
    id: string;
    brokerAccountId: string;
    weekStart: Date;
    startingEquity: { toString(): string };
    currentEquity: { toString(): string };
    realizedPnl: { toString(): string };
    unrealizedPnl: { toString(): string };
    lossLimit: { toString(): string };
    isTripped: boolean;
    trippedAt: Date | null;
    resetByUserId: string | null;
    resetAt: Date | null;
    resetNote: string | null;
  }) => ({
    id: w.id,
    brokerAccountId: w.brokerAccountId,
    weekStart: w.weekStart.toISOString().slice(0, 10),
    startingEquity: w.startingEquity.toString(),
    currentEquity: w.currentEquity.toString(),
    realizedPnl: w.realizedPnl.toString(),
    unrealizedPnl: w.unrealizedPnl.toString(),
    lossLimit: w.lossLimit.toString(),
    isTripped: w.isTripped,
    trippedAt: w.trippedAt?.toISOString() ?? null,
    resetByUserId: w.resetByUserId,
    resetAt: w.resetAt?.toISOString() ?? null,
    resetNote: w.resetNote,
  });

  return {
    async getLatestWeeklySnapshot() {
      const w = await prisma.weeklyRiskSnapshot.findFirst({
        orderBy: { weekStart: "desc" },
      });
      return w ? serialize(w) : null;
    },
    async resetWeeklySnapshot({ id, userId, note, resetAt }) {
      const updated = await prisma.weeklyRiskSnapshot.update({
        where: { id },
        data: {
          isTripped: false,
          resetByUserId: userId,
          resetAt,
          resetNote: note,
        },
      });
      return serialize(updated);
    },
    async writeAuditLog(input) {
      await prisma.auditLog.create({
        data: {
          actorId: input.actorId,
          action: input.action,
          entityType: input.entityType,
          entityId: input.entityId,
          metadata: input.metadata as Prisma.InputJsonValue,
          ip: input.ip,
        },
      });
    },
    async notifySettingsUpdated(reason) {
      await internalRepo.publishSseEvent({
        id: crypto.randomUUID(),
        type: "settings.updated",
        timestamp: new Date().toISOString(),
        data: { reason },
      });
    },
  };
}

function serializeProposal(p: {
  id: string;
  dedupKey: string;
  brokerAccountId: string;
  strategyId: string;
  symbol: string;
  timeframe: string;
  direction: "BUY" | "SELL";
  entry: { toString(): string };
  stopLoss: { toString(): string };
  takeProfit: { toString(): string };
  volume: { toString(): string };
  riskAmount: { toString(): string };
  riskPercent: { toString(): string };
  rewardRisk: { toString(): string };
  estimatedMargin: { toString(): string } | null;
  spreadAtProposal: { toString(): string };
  rationale: string;
  aiStatus: "COMPLETED" | "VETOED" | "UNAVAILABLE" | "SKIPPED";
  aiModel: string | null;
  aiPromptVersion: string | null;
  aiLatencyMs: number | null;
  aiTokensUsed: number | null;
  aiRawResponse: unknown;
  status: string;
  rejectionReason: string | null;
  expiresAt: Date;
  claimedAt: Date | null;
  createdAt: Date;
  updatedAt: Date;
  brokerAccount?: {
    login: string;
    accountCurrency: string;
    currencyScale: number;
  };
  strategy?: { name: string; version: string };
}): ProposalRecord {
  return {
    id: p.id,
    dedupKey: p.dedupKey,
    brokerAccountId: p.brokerAccountId,
    accountLogin: p.brokerAccount?.login,
    accountCurrency: p.brokerAccount?.accountCurrency,
    currencyScale: p.brokerAccount?.currencyScale,
    strategyId: p.strategyId,
    strategyName: p.strategy
      ? `${p.strategy.name}@${p.strategy.version}`
      : undefined,
    symbol: p.symbol,
    timeframe: p.timeframe,
    direction: p.direction,
    entry: p.entry.toString(),
    stopLoss: p.stopLoss.toString(),
    takeProfit: p.takeProfit.toString(),
    volume: p.volume.toString(),
    riskAmount: p.riskAmount.toString(),
    riskPercent: p.riskPercent.toString(),
    rewardRisk: p.rewardRisk.toString(),
    estimatedMargin: p.estimatedMargin?.toString() ?? null,
    spreadAtProposal: p.spreadAtProposal.toString(),
    rationale: p.rationale,
    aiStatus: p.aiStatus,
    aiModel: p.aiModel,
    aiPromptVersion: p.aiPromptVersion,
    aiLatencyMs: p.aiLatencyMs,
    aiTokensUsed: p.aiTokensUsed,
    aiRawResponse: (p.aiRawResponse ?? null) as Record<string, unknown> | null,
    status: p.status,
    rejectionReason: p.rejectionReason,
    expiresAt: p.expiresAt.toISOString(),
    claimedAt: p.claimedAt?.toISOString() ?? null,
    createdAt: p.createdAt.toISOString(),
    updatedAt: p.updatedAt.toISOString(),
  };
}

function createPrismaProposalRepo(
  internalRepo: InternalWorkerRepository,
): ProposalWorkflowRepository {
  return {
    async listProposals({ status, limit }) {
      const rows = await prisma.tradeProposal.findMany({
        where: status ? { status: status as never } : undefined,
        include: {
          brokerAccount: {
            select: { login: true, accountCurrency: true, currencyScale: true },
          },
          strategy: { select: { name: true, version: true } },
        },
        orderBy: { createdAt: "desc" },
        take: limit,
      });
      return rows.map(serializeProposal);
    },

    async findProposalById(id) {
      const p = await prisma.tradeProposal.findUnique({
        where: { id },
        include: {
          brokerAccount: {
            select: { login: true, accountCurrency: true, currencyScale: true },
          },
          strategy: { select: { name: true, version: true } },
        },
      });
      return p ? serializeProposal(p) : null;
    },

    async isSystemBlockedForNewOrders() {
      const settings = await prisma.tradingSettings.findFirst({
        orderBy: { updatedAt: "desc" },
      });
      if (settings?.killSwitchActive) {
        return { blocked: true, reason: "Kill switch is active" };
      }
      if (settings?.isPaused) {
        return { blocked: true, reason: "Trading system is paused" };
      }
      try {
        await ensureRedisConnected();
        if ((await redis.get(REDIS_KEYS.KILL_SWITCH_FLAG)) === "1") {
          return { blocked: true, reason: "Kill switch is active" };
        }
      } catch {
        // Non-fatal; DB check already applied
      }
      return { blocked: false };
    },

    async hasApprovalIdempotencyKey(idempotencyKey) {
      const found = await prisma.tradeApproval.findUnique({
        where: { idempotencyKey },
        select: { id: true },
      });
      return found !== null;
    },

    async approveProposalAtomic({
      proposalId,
      userId,
      idempotencyKey,
      comment,
      now,
    }) {
      return prisma.$transaction(async (tx) => {
        const settingsRow = await tx.tradingSettings.findFirst({
          orderBy: { updatedAt: "desc" },
        });
        const maxDaily = settingsRow?.maxDailyTrades ?? 5;
        const propMeta = await tx.tradeProposal.findUnique({
          where: { id: proposalId },
          select: { brokerAccountId: true },
        });
        if (propMeta) {
          const todayCount = await countTodayOpenTrades(
            propMeta.brokerAccountId,
            now,
          );
          if (todayCount >= maxDaily) {
            return {
              ok: false as const,
              code: 409,
              error: `Daily max trades limit (${todayCount}/${maxDaily}) reached for today (UTC)`,
            };
          }
        }
        const updated = await tx.tradeProposal.updateMany({
          where: {
            id: proposalId,
            status: "PENDING_APPROVAL",
            expiresAt: { gt: now },
          },
          data: { status: "APPROVED" },
        });
        if (updated.count === 0) {
          const existing = await tx.tradeProposal.findUnique({
            where: { id: proposalId },
            select: { status: true, expiresAt: true },
          });
          if (!existing) {
            return { ok: false as const, code: 404, error: "Proposal not found" };
          }
          if (existing.expiresAt <= now) {
            return {
              ok: false as const,
              code: 409,
              error: "Proposal has expired",
            };
          }
          return {
            ok: false as const,
            code: 409,
            error: `Proposal cannot be approved in status ${existing.status}`,
          };
        }

        const approval = await tx.tradeApproval.create({
          data: {
            proposalId,
            userId,
            decision: "APPROVE",
            approvedAt: now,
            idempotencyKey,
            comment,
          },
          select: { id: true },
        });

        const proposal = await tx.tradeProposal.findUniqueOrThrow({
          where: { id: proposalId },
          include: {
            brokerAccount: {
              select: {
                login: true,
                accountCurrency: true,
                currencyScale: true,
              },
            },
            strategy: { select: { name: true, version: true } },
          },
        });

        return {
          ok: true as const,
          proposal: serializeProposal(proposal),
          approvalId: approval.id,
        };
      });
    },

    async rejectProposal({ proposalId, userId, idempotencyKey, comment, now }) {
      return prisma.$transaction(async (tx) => {
        const updated = await tx.tradeProposal.updateMany({
          where: { id: proposalId, status: "PENDING_APPROVAL" },
          data: {
            status: "REJECTED",
            rejectionReason: comment ?? "Rejected by owner",
          },
        });
        if (updated.count === 0) return null;

        await tx.tradeApproval.create({
          data: {
            proposalId,
            userId,
            decision: "REJECT",
            approvedAt: now,
            idempotencyKey,
            comment,
          },
        });

        const settings = await tx.tradingSettings.findFirst({
          orderBy: { updatedAt: "desc" },
        });
        if (settings) {
          const cooldownUntil = new Date(
            now.getTime() + settings.postRejectionCooldownMinutes * 60_000,
          );
          await tx.tradingSettings.update({
            where: { id: settings.id },
            data: { cooldownUntil },
          });
        }

        const p = await tx.tradeProposal.findUniqueOrThrow({
          where: { id: proposalId },
          include: {
            brokerAccount: {
              select: {
                login: true,
                accountCurrency: true,
                currencyScale: true,
              },
            },
            strategy: { select: { name: true, version: true } },
          },
        });
        return serializeProposal(p);
      });
    },

    async cancelProposal(proposalId) {
      const updated = await prisma.tradeProposal.updateMany({
        where: { id: proposalId, status: "PENDING_APPROVAL" },
        data: { status: "CANCELLED" },
      });
      if (updated.count === 0) return null;
      const p = await prisma.tradeProposal.findUniqueOrThrow({
        where: { id: proposalId },
        include: {
          brokerAccount: {
            select: { login: true, accountCurrency: true, currencyScale: true },
          },
          strategy: { select: { name: true, version: true } },
        },
      });
      return serializeProposal(p);
    },

    async enqueueCommand(cmd) {
      await ensureRedisConnected();
      const entryId = await redis.xadd(
        REDIS_KEYS.COMMAND_STREAM,
        "*",
        "payload",
        JSON.stringify(cmd),
      );
      return entryId ?? "0-0";
    },

    async writeAuditLog(input) {
      await prisma.auditLog.create({
        data: {
          actorId: input.actorId,
          action: input.action,
          entityType: input.entityType,
          entityId: input.entityId,
          metadata: input.metadata as Prisma.InputJsonValue,
          ip: input.ip,
        },
      });
    },

    async publishSseEvent(event) {
      await internalRepo.publishSseEvent(event);
    },
  };
}

function createPrismaSystemControlRepo(
  internalRepo: InternalWorkerRepository,
): SystemControlRepository {
  return {
    async activateKillSwitch(reason) {
      try {
        await ensureRedisConnected();
        await redis.set(REDIS_KEYS.KILL_SWITCH_FLAG, "1");
      } catch (err) {
        logger.error({ err }, "Failed to set Redis kill switch flag");
      }

      const settings = await prisma.tradingSettings.findFirst({
        orderBy: { updatedAt: "desc" },
      });
      if (settings) {
        await prisma.tradingSettings.update({
          where: { id: settings.id },
          data: { killSwitchActive: true, isPaused: true },
        });
      }

      const cancelled = await prisma.tradeProposal.updateMany({
        where: { status: { in: ["PENDING_APPROVAL", "APPROVED"] } },
        data: {
          status: "CANCELLED",
          rejectionReason: reason ?? "Kill switch activated",
        },
      });

      return { cancelledProposalsCount: cancelled.count };
    },

    async setPaused(isPaused) {
      const settings = await prisma.tradingSettings.findFirst({
        orderBy: { updatedAt: "desc" },
      });
      if (settings) {
        await prisma.tradingSettings.update({
          where: { id: settings.id },
          data: { isPaused },
        });
      }
    },

    async resumeSystem(clearKillSwitch) {
      const weekly = await prisma.weeklyRiskSnapshot.findFirst({
        orderBy: { weekStart: "desc" },
      });
      if (weekly?.isTripped && !weekly.resetAt) {
        return {
          ok: false,
          error:
            "Weekly loss limit is tripped; perform manual review/reset before resuming",
        };
      }

      const settings = await prisma.tradingSettings.findFirst({
        orderBy: { updatedAt: "desc" },
      });
      if (settings?.killSwitchActive && !clearKillSwitch) {
        return {
          ok: false,
          error:
            "Kill switch is active; pass clearKillSwitch=true to explicitly clear it",
        };
      }

      if (clearKillSwitch) {
        try {
          await ensureRedisConnected();
          await redis.set(REDIS_KEYS.KILL_SWITCH_FLAG, "0");
        } catch (err) {
          logger.warn({ err }, "Failed to clear Redis kill switch flag");
        }
      }

      if (settings) {
        await prisma.tradingSettings.update({
          where: { id: settings.id },
          data: {
            isPaused: false,
            ...(clearKillSwitch ? { killSwitchActive: false } : {}),
          },
        });
      }
      return { ok: true };
    },

    async writeAuditLog(input) {
      await prisma.auditLog.create({
        data: {
          actorId: input.actorId,
          action: input.action,
          entityType: input.entityType,
          entityId: input.entityId,
          metadata: input.metadata as Prisma.InputJsonValue,
          ip: input.ip,
        },
      });
    },

    async publishSseEvent(event) {
      await internalRepo.publishSseEvent(event);
    },
  };
}

function serializePosition(p: {
  id: string;
  brokerAccountId: string;
  brokerPositionId: string;
  symbol: string;
  side: "BUY" | "SELL";
  volume: { toString(): string };
  openPrice: { toString(): string };
  currentPrice: { toString(): string };
  closePrice: { toString(): string } | null;
  stopLoss: { toString(): string } | null;
  takeProfit: { toString(): string } | null;
  profit: { toString(): string };
  commission: { toString(): string };
  swap: { toString(): string };
  magicNumber: number | null;
  isBotManaged: boolean;
  status: "OPEN" | "CLOSING" | "CLOSED" | "UNKNOWN";
  closeRequestedAt: Date | null;
  openedAt: Date;
  closedAt: Date | null;
  brokerAccount?: {
    login: string;
    accountCurrency: string;
    currencyScale: number;
  };
}): PositionRecord {
  return {
    id: p.id,
    brokerAccountId: p.brokerAccountId,
    accountLogin: p.brokerAccount?.login,
    accountCurrency: p.brokerAccount?.accountCurrency,
    currencyScale: p.brokerAccount?.currencyScale,
    brokerPositionId: p.brokerPositionId,
    symbol: p.symbol,
    side: p.side,
    volume: p.volume.toString(),
    openPrice: p.openPrice.toString(),
    currentPrice: p.currentPrice.toString(),
    closePrice: p.closePrice?.toString() ?? null,
    stopLoss: p.stopLoss?.toString() ?? null,
    takeProfit: p.takeProfit?.toString() ?? null,
    profit: p.profit.toString(),
    commission: p.commission.toString(),
    swap: p.swap.toString(),
    magicNumber: p.magicNumber,
    isBotManaged: p.isBotManaged,
    status: p.status,
    closeRequestedAt: p.closeRequestedAt?.toISOString() ?? null,
    openedAt: p.openedAt.toISOString(),
    closedAt: p.closedAt?.toISOString() ?? null,
  };
}

function createPrismaPositionsRepo(
  internalRepo: InternalWorkerRepository,
): PositionsAndOrdersRepository {
  return {
    async listPositions({ status, limit }) {
      const rows = await prisma.position.findMany({
        where: status ? { status } : undefined,
        include: {
          brokerAccount: {
            select: { login: true, accountCurrency: true, currencyScale: true },
          },
        },
        orderBy: { openedAt: "desc" },
        take: limit,
      });
      return rows.map(serializePosition);
    },

    async listOrders(limit) {
      const rows = await prisma.tradeOrder.findMany({
        orderBy: { createdAt: "desc" },
        take: limit,
      });
      return rows.map((o) => ({
        id: o.id,
        proposalId: o.proposalId,
        positionId: o.positionId,
        actionType: o.actionType,
        brokerOrderId: o.brokerOrderId,
        brokerDealId: o.brokerDealId,
        symbol: o.symbol,
        side: o.side,
        volume: o.volume.toString(),
        requestedPrice: o.requestedPrice?.toString() ?? null,
        entryPrice: o.entryPrice?.toString() ?? null,
        stopLoss: o.stopLoss?.toString() ?? null,
        takeProfit: o.takeProfit?.toString() ?? null,
        fillingMode: o.fillingMode,
        deviationPoints: o.deviationPoints,
        retcode: o.retcode,
        brokerComment: o.brokerComment,
        status: o.status,
        createdAt: o.createdAt.toISOString(),
      }));
    },

    async requestManualCloseAtomic({ positionId, idempotencyKey, now }) {
      const dup = await prisma.position.findUnique({
        where: { closeIdempotencyKey: idempotencyKey },
        select: { id: true },
      });
      if (dup) {
        return {
          ok: false,
          code: 409,
          error: "Duplicate close request (idempotencyKey already used)",
        };
      }

      const updated = await prisma.position.updateMany({
        where: { id: positionId, status: "OPEN" },
        data: {
          status: "CLOSING",
          closeRequestedAt: now,
          closeIdempotencyKey: idempotencyKey,
        },
      });
      if (updated.count === 0) {
        const existing = await prisma.position.findUnique({
          where: { id: positionId },
          select: { status: true },
        });
        if (!existing) {
          return { ok: false, code: 404, error: "Position not found" };
        }
        return {
          ok: false,
          code: 409,
          error: `Position cannot be closed in status ${existing.status}`,
        };
      }

      const pos = await prisma.position.findUniqueOrThrow({
        where: { id: positionId },
        include: {
          brokerAccount: {
            select: { login: true, accountCurrency: true, currencyScale: true },
          },
        },
      });
      return { ok: true, position: serializePosition(pos) };
    },

    async enqueueCloseCommand(cmd) {
      await ensureRedisConnected();
      const entryId = await redis.xadd(
        REDIS_KEYS.COMMAND_STREAM,
        "*",
        "payload",
        JSON.stringify(cmd),
      );
      return entryId ?? "0-0";
    },

    async writeAuditLog(input) {
      await prisma.auditLog.create({
        data: {
          actorId: input.actorId,
          action: input.action,
          entityType: input.entityType,
          entityId: input.entityId,
          metadata: input.metadata as Prisma.InputJsonValue,
          ip: input.ip,
        },
      });
    },

    async publishSseEvent(event) {
      await internalRepo.publishSseEvent(event);
    },
  };
}

function createPrismaDashboardRepo(): DashboardAndReportsRepository {
  const ddPercent = (start: string, curr: string) => {
    const s = Number(start);
    const c = Number(curr);
    if (!Number.isFinite(s) || s <= 0 || !Number.isFinite(c)) return 0;
    return Number(Math.max(0, ((s - c) / s) * 100).toFixed(2));
  };

  return {
    async getDashboardSummary() {
      const [account, settings, openPositions, pendingProposals] =
        await Promise.all([
          prisma.brokerAccount.findFirst({
            where: { isEnabled: true },
            orderBy: { updatedAt: "desc" },
          }),
          prisma.tradingSettings.findFirst({ orderBy: { updatedAt: "desc" } }),
          prisma.position.count({
            where: { status: { in: ["OPEN", "CLOSING"] } },
          }),
          prisma.tradeProposal.count({
            where: { status: "PENDING_APPROVAL", expiresAt: { gt: new Date() } },
          }),
        ]);

      let daily = null;
      let weekly = null;
      if (account) {
        const [dSnap, wSnap] = await Promise.all([
          prisma.dailyRiskSnapshot.findFirst({
            where: { brokerAccountId: account.id },
            orderBy: { date: "desc" },
          }),
          prisma.weeklyRiskSnapshot.findFirst({
            where: { brokerAccountId: account.id },
            orderBy: { weekStart: "desc" },
          }),
        ]);
        if (dSnap) {
          daily = {
            startingEquity: dSnap.startingEquity.toString(),
            currentEquity: dSnap.currentEquity.toString(),
            lossLimit: dSnap.lossLimit.toString(),
            drawdownPercent: ddPercent(
              dSnap.startingEquity.toString(),
              dSnap.currentEquity.toString(),
            ),
            isTripped: dSnap.isTripped,
          };
        }
        if (wSnap) {
          weekly = {
            startingEquity: wSnap.startingEquity.toString(),
            currentEquity: wSnap.currentEquity.toString(),
            lossLimit: wSnap.lossLimit.toString(),
            drawdownPercent: ddPercent(
              wSnap.startingEquity.toString(),
              wSnap.currentEquity.toString(),
            ),
            isTripped: wSnap.isTripped,
            resetAt: wSnap.resetAt?.toISOString() ?? null,
          };
        }
      }

      const scale = account && account.currencyScale > 0 ? account.currencyScale : 1;
      const toUsd = (v: string) => {
        const n = Number(v);
        return Number.isFinite(n) ? (n / scale).toFixed(2) : "0.00";
      };

      const dailyTradesCount = account
        ? await countTodayOpenTrades(account.id)
        : 0;

      return {
        account: account
          ? {
              login: account.login,
              server: account.server,
              mode: account.mode,
              accountType: account.accountType,
              accountCurrency: account.accountCurrency,
              currencyScale: account.currencyScale,
              balance: account.balance.toString(),
              equity: account.equity.toString(),
              freeMargin: account.freeMargin.toString(),
              usdEquivalent: {
                balance: toUsd(account.balance.toString()),
                equity: toUsd(account.equity.toString()),
                freeMargin: toUsd(account.freeMargin.toString()),
              },
            }
          : null,
        controls: {
          isPaused: Boolean(settings?.isPaused),
          killSwitchActive: Boolean(settings?.killSwitchActive),
          demoAutoExecute:
            Boolean(settings?.demoAutoExecute) &&
            account?.mode === "DEMO" &&
            !account?.liveUnlockedAt,
        },
        counts: {
          openPositions,
          pendingProposals,
          dailyTradesCount,
          maxDailyTrades: settings?.maxDailyTrades ?? 5,
        },
        risk: { daily, weekly },
      };
    },

    async getClosedTradesForReport({ from, to }) {
      const account = await prisma.brokerAccount.findFirst({
        where: { isEnabled: true },
        orderBy: { updatedAt: "desc" },
        select: { accountCurrency: true, currencyScale: true },
      });
      const rows = await prisma.position.findMany({
        where: {
          status: "CLOSED",
          ...(from || to
            ? {
                closedAt: {
                  ...(from ? { gte: from } : {}),
                  ...(to ? { lte: to } : {}),
                },
              }
            : {}),
        },
        orderBy: { closedAt: "asc" },
      });
      return {
        accountCurrency: account?.accountCurrency ?? "USC",
        currencyScale: account?.currencyScale ?? 100,
        trades: rows.map((r) => ({
          id: r.id,
          symbol: r.symbol,
          side: r.side,
          volume: r.volume.toString(),
          profit: r.profit.toString(),
          commission: r.commission.toString(),
          swap: r.swap.toString(),
          closedAt: (r.closedAt ?? r.updatedAt).toISOString(),
        })),
      };
    },

    async listAuditLogs({ action, limit }) {
      const rows = await prisma.auditLog.findMany({
        where: action ? { action } : undefined,
        include: { actor: { select: { email: true } } },
        orderBy: { createdAt: "desc" },
        take: limit,
      });
      return rows.map((r) => ({
        id: r.id,
        actorId: r.actorId,
        actorEmail: r.actor?.email ?? null,
        action: r.action,
        entityType: r.entityType,
        entityId: r.entityId,
        metadata: (r.metadata ?? {}) as Record<string, unknown>,
        ip: r.ip,
        createdAt: r.createdAt.toISOString(),
      }));
    },
  };
}

export function createApp(deps: AppDependencies = {}) {
  const config = deps.config ?? env;
  const sseHub = deps.sseHub ?? new SseHub();
  const authStore = deps.authStore ?? createPrismaAuthStore();
  const authRepo = deps.authRepo ?? createPrismaAuthRepo();
  const healthChecker = deps.healthChecker ?? createDefaultHealthChecker();
  const internalRepo = deps.internalRepo ?? createPrismaInternalRepo(sseHub);
  const accountRepo =
    deps.accountRepo ?? createPrismaAccountRepo(internalRepo);
  const marketRepo = deps.marketRepo ?? createPrismaMarketRepo();
  const strategyRepo =
    deps.strategyRepo ?? createPrismaStrategyRepo(internalRepo);
  const blackoutRepo =
    deps.blackoutRepo ?? createPrismaBlackoutRepo(internalRepo);
  const settingsRepo =
    deps.settingsRepo ?? createPrismaSettingsRepo(internalRepo);
  const riskRepo = deps.riskRepo ?? createPrismaRiskRepo(internalRepo);
  const proposalRepo =
    deps.proposalRepo ?? createPrismaProposalRepo(internalRepo);
  const systemControlRepo =
    deps.systemControlRepo ?? createPrismaSystemControlRepo(internalRepo);
  const positionsRepo =
    deps.positionsRepo ?? createPrismaPositionsRepo(internalRepo);
  const dashboardRepo = deps.dashboardRepo ?? createPrismaDashboardRepo();

  const app = express();
  app.set("trust proxy", 1);

  const allowedOrigins = config.CORS_ORIGIN.split(",")
    .map((s) => s.trim())
    .filter(Boolean);
  app.use(helmet());
  app.use(
    cors({
      origin: allowedOrigins.length <= 1 ? allowedOrigins[0] : allowedOrigins,
      credentials: true,
    }),
  );
  app.use(express.json({ limit: "1mb" }));
  app.use(cookieParser());
  app.use(pinoHttp({ logger }));

  const { requireAuth, requireOwner, requireCsrf, requireWorkerToken } =
    createAuthMiddleware({
      authStore,
      cookieName: config.SESSION_COOKIE_NAME,
      workerAuthToken: config.WORKER_AUTH_TOKEN,
    });

  app.use(createHealthRouter({ checker: healthChecker, requireAuth }));
  app.use(
    createAuthRouter({
      repo: authRepo,
      requireAuth,
      requireCsrf,
      cookieName: config.SESSION_COOKIE_NAME,
      sessionTtlHours: config.SESSION_TTL_HOURS,
      isProduction: config.NODE_ENV === "production",
    }),
  );
  app.use(
    createAccountRouter({
      repo: accountRepo,
      requireAuth,
      requireOwner,
      requireCsrf,
    }),
  );
  app.use(createMarketRouter({ repo: marketRepo, requireAuth }));
  app.use(
    createStrategiesRouter({
      repo: strategyRepo,
      requireAuth,
      requireOwner,
      requireCsrf,
    }),
  );
  app.use(
    createBlackoutsRouter({
      repo: blackoutRepo,
      requireAuth,
      requireOwner,
      requireCsrf,
    }),
  );
  app.use(
    createSettingsRouter({
      repo: settingsRepo,
      requireAuth,
      requireOwner,
      requireCsrf,
    }),
  );
  app.use(
    createRiskRouter({
      repo: riskRepo,
      requireAuth,
      requireOwner,
      requireCsrf,
    }),
  );
  app.use(
    createProposalsRouter({
      repo: proposalRepo,
      requireAuth,
      requireOwner,
      requireCsrf,
    }),
  );
  app.use(
    createSystemControlRouter({
      repo: systemControlRepo,
      requireAuth,
      requireOwner,
      requireCsrf,
    }),
  );
  app.use(
    createPositionsRouter({
      repo: positionsRepo,
      requireAuth,
      requireOwner,
      requireCsrf,
    }),
  );
  app.use(createDashboardRouter({ repo: dashboardRepo, requireAuth }));
  app.use(createStreamRouter({ sseHub, requireAuth }));
  app.use(createInternalRouter({ repo: internalRepo, requireWorkerToken }));

  app.get("/api/v1/owner/check", requireAuth, requireOwner, (req, res) => {
    res.status(200).json({ ok: true, ownerId: req.authUser?.id });
  });

  app.use(errorHandler);
  return app;
}

import "dotenv/config";
import bcrypt from "bcryptjs";
import { PrismaClient, AiFallbackMode, UserRole } from "@prisma/client";

const prisma = new PrismaClient();

async function main() {
  const ownerEmail = process.env.OWNER_EMAIL ?? "owner@local.dev";
  const ownerPassword =
    process.env.OWNER_PASSWORD ?? "ChangeMe123!StrongPassword";

  const passwordHash = await bcrypt.hash(ownerPassword, 12);

  const owner = await prisma.user.upsert({
    where: { email: ownerEmail },
    update: {},
    create: {
      email: ownerEmail,
      passwordHash,
      role: UserRole.OWNER,
      isActive: true,
    },
  });

  const existingSettings = await prisma.tradingSettings.findFirst();
  if (!existingSettings) {
    await prisma.tradingSettings.create({
      data: {
        symbol: process.env.MT5_SYMBOL ?? "XAUUSDc",
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
        sessionWindows: {
          timezone: "UTC",
          rolloverStartUtc: "21:00",
          rolloverEndUtc: "22:00",
          allowedSessions: ["LONDON", "NEW_YORK"],
        },
        aiFallbackMode: AiFallbackMode.DETERMINISTIC_ONLY,
        isPaused: false,
        killSwitchActive: false,
      },
    });
  }

  await prisma.strategy.upsert({
    where: {
      name_version: {
        name: "xauusd_v1",
        version: "1.0.0",
      },
    },
    update: {},
    create: {
      name: "xauusd_v1",
      version: "1.0.0",
      timeframe: "M15",
      isActive: true,
      parameters: {
        higherTimeframe: "H1",
        setupTimeframe: "M15",
        confirmTimeframe: "M5",
        emaFast: 20,
        emaMid: 50,
        emaSlow: 200,
        rsiPeriod: 14,
        atrPeriod: 14,
        slAtrBufferMultiplier: 0.5,
        targetRewardRisk: 2.0,
      },
    },
  });

  await prisma.strategy.upsert({
    where: {
      name_version: {
        name: "xauusd_ew_v1",
        version: "1.0.0",
      },
    },
    update: {},
    create: {
      name: "xauusd_ew_v1",
      version: "1.0.0",
      timeframe: "M15",
      isActive: true,
      parameters: {
        higherTimeframe: "H1",
        setupTimeframe: "M15",
        emaFast: 21,
        emaSlow: 144,
        rsiPeriod: 14,
        atrPeriod: 14,
        zigzagAtrMult: 1.5,
        slAtrBufferMultiplier: 0.75,
        targetRewardRisk: 2.0,
        minSmcConfluence: 2,
        minAnchorDivergences: 2,
        enableWave3: true,
        enableWave5: true,
        enableAbc: true,
      },
    },
  });

  await prisma.auditLog.create({
    data: {
      actorId: owner.id,
      action: "SYSTEM_SEED",
      entityType: "System",
      metadata: { seededAt: new Date().toISOString() },
    },
  });

  console.log(`Seed complete. Owner: ${owner.email}`);
}

main()
  .catch((err) => {
    console.error("Seed failed:", err);
    process.exit(1);
  })
  .finally(async () => {
    await prisma.$disconnect();
  });

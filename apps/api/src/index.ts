import { createApp, startRedisSseBridge } from "./app.js";
import { env } from "./config/env.js";
import { logger } from "./lib/logger.js";
import { prisma } from "./lib/prisma.js";
import { redis } from "./lib/redis.js";
import { SseHub } from "./lib/sseHub.js";

const sseHub = new SseHub();
const app = createApp({ sseHub });
const subscriber = startRedisSseBridge(sseHub, env.REDIS_URL);

const server = app.listen(env.API_PORT, () => {
  logger.info(
    { port: env.API_PORT, env: env.NODE_ENV },
    "Trading Agent API listening",
  );
});

async function shutdown(signal: string) {
  logger.info({ signal }, "Shutting down API");
  sseHub.closeAll();
  subscriber.disconnect();
  server.close(async () => {
    await prisma.$disconnect().catch(() => {});
    redis.disconnect();
    process.exit(0);
  });
}

process.on("SIGINT", () => void shutdown("SIGINT"));
process.on("SIGTERM", () => void shutdown("SIGTERM"));

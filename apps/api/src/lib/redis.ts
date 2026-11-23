import { Redis } from "ioredis";
import { env } from "../config/env.js";
import { logger } from "./logger.js";

export function createRedisClient(url = env.REDIS_URL): Redis {
  const isTls = url.startsWith("rediss://");
  const client = new Redis(url, {
    lazyConnect: true,
    maxRetriesPerRequest: 2,
    enableReadyCheck: !isTls,
    ...(isTls ? { tls: {} } : {}),
    retryStrategy(times) {
      if (times > 5) return null;
      return Math.min(times * 200, 2000);
    },
  });

  client.on("error", (err) => {
    logger.warn({ err: err.message }, "Redis client error");
  });

  return client;
}

export const redis = createRedisClient();

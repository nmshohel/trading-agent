import "dotenv/config";
import { z } from "zod";

const EnvSchema = z.object({
  NODE_ENV: z
    .enum(["development", "test", "production"])
    .default("development"),
  API_PORT: z.coerce.number().int().positive().default(5000),
  CORS_ORIGIN: z.string().trim().default("http://localhost:3000"),
  DATABASE_URL: z
    .string()
    .trim()
    .min(1)
    .default(
      "postgresql://trading_agent:change_me_pg_password@127.0.0.1:5432/trading_agent?schema=public",
    ),
  REDIS_URL: z
    .string()
    .trim()
    .min(1)
    .default("redis://:change_me_redis_password@127.0.0.1:6379"),
  APP_SESSION_SECRET: z
    .string()
    .trim()
    .min(16)
    .default("dev_only_replace_with_long_random_session_secret"),
  SESSION_COOKIE_NAME: z.string().trim().min(1).default("ta_session"),
  SESSION_TTL_HOURS: z.coerce.number().int().positive().default(24),
  WORKER_AUTH_TOKEN: z
    .string()
    .trim()
    .min(16)
    .default("dev_only_replace_with_long_random_worker_token"),
});

export type AppEnv = z.infer<typeof EnvSchema>;

export function loadEnv(source: NodeJS.ProcessEnv = process.env): AppEnv {
  return EnvSchema.parse(source);
}

export const env = loadEnv();

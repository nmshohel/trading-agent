export const REDIS_KEYS = {
  COMMAND_STREAM: "commands:trading",
  COMMAND_DLQ_STREAM: "commands:trading:dlq",
  COMMAND_CONSUMER_GROUP: "trading-workers",
  SSE_PUBSUB_CHANNEL: "events:sse",
  KILL_SWITCH_FLAG: "system:kill_switch",
  WORKER_HEARTBEAT: "worker:heartbeat",
  executionLock: (accountLogin: string) => `lock:execution:${accountLogin}`,
} as const;

export const DEFAULT_RISK_LIMITS = {
  RISK_PERCENT: "0.25",
  MAX_RISK_PERCENT: "0.50",
  DAILY_LOSS_LIMIT_PERCENT: "1.00",
  WEEKLY_LOSS_LIMIT_PERCENT: "2.00",
  MAX_OPEN_POSITIONS: 1,
  MAX_PENDING_PROPOSALS: 3,
  MIN_REWARD_RISK: "2.00",
  APPROVAL_EXPIRY_SECONDS: 120,
  POST_REJECTION_COOLDOWN_MINUTES: 15,
  ROLLOVER_BLACKOUT_MINUTES: 60,
  STUCK_STATE_TIMEOUT_SECONDS: 45,
} as const;

export const CURRENCY_SCALE = {
  USD: 1,
  USC: 100,
} as const;

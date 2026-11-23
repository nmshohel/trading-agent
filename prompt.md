# Step-by-Step Build Prompts — Personal AI Forex Trading Agent

This guide breaks down building the **Personal AI Forex Trading Agent** from [architecture.md](./architecture.md) (v1.2) into **11 sequential phases (Phase 0 – Phase 10)**.

## How to Use This File

1. Run **one phase at a time** in order. Do not skip ahead to AI prompts or order execution before earlier phases are verified.
2. Copy the **Prompt** block for the current phase and send it to your coding agent.
3. Verify every item in the phase's **Exit Gate** (build, tests, manual checks) before starting the next phase.
4. Keep every session grounded in `architecture.md`—never silently relax risk checks, trust boundaries, or DEMO-first defaults.

---

## Global Rules (Applies to Every Phase)

> Always read `architecture.md` first before editing or creating code. Follow these non-negotiable invariants across all phases:
>
> - **Single DB owner:** Only `apps/api` (Node.js + Express + Prisma) connects to PostgreSQL. `apps/worker` (Python) never accesses PostgreSQL directly; it uses authenticated `/api/internal/v1/*` endpoints and Redis Streams/PubSub.
> - **Financial precision & time:** Use `Decimal` (Prisma `Decimal` / Python `Decimal`) for money, prices, volumes, and percentages—never JS floats. Store all timestamps in UTC.
> - **Standard + Cent (`XAUUSDc` / `USC`) support:** Always read live MT5 symbol specs (`trade_contract_size`, `volume_min`, `volume_step`, `filling_mode`, `trade_stops_level`, etc.). Run broker math in native account currency and use `currencyScale` (`1` for `USD`, `100` for `USC`) for USD equivalent UI display.
> - **MT5 thread-safety:** All `MetaTrader5` Python calls must go through a single centralized `MT5Client` guarded by `threading.RLock()`.
> - **Deterministic safety & fail-closed:** AI never chooses prices, SL/TP, or lot sizes and never calls MT5. Every new order requires single-use human approval + mandatory SL + pre-execution revalidation inside a Redis lock. Manual close is audited and risk-reducing (allowed even during pause/kill switch). Fail closed on disconnects or uncertain states.
> - **Secrets:** Never commit `.env`, credentials, API keys, or MT5 passwords. Use placeholders in `.env.example`.

---

## Phase 0 — Project Foundation, Monorepo & Database Schema

### Goal
Bootstrap the monorepo (`apps/web`, `apps/api`, `apps/worker`, `packages/shared`, `infra/`), local Docker dev infrastructure (PostgreSQL + Redis), complete Prisma schema, Express API skeleton with auth/CSRF/validation, and Next.js dashboard skeleton.

### Prompt

```markdown
Read `architecture.md` (v1.2) thoroughly and implement **Phase 0: Project Foundation**.

Build the following:
1. **Monorepo structure**:
   - `apps/web` — Next.js (App Router), TypeScript, Tailwind CSS, shadcn/ui, TanStack Query, React Hook Form, Zod. Configure same-origin `/api/*` rewrite proxy to `apps/api` for dev.
   - `apps/api` — Node.js, Express, TypeScript, Prisma ORM, Zod, `ioredis`, structured JSON logger (e.g., `pino`), Helmet, rate limiting, CSRF protection, and HTTP-only cookie session authentication (`/api/v1/auth/login`, `/logout`, `/me`) with owner role checks.
   - `apps/worker` — Python 3.x project skeleton (`app/`, `backtest/`, `tests/`, `requirements.txt` with pinned versions, `pyproject.toml`/linting, `.env.example`).
   - `packages/shared` — Shared TypeScript types, Zod schemas, SSE event types, and Redis Stream command contracts (`EXECUTE_PROPOSAL`, `CLOSE_POSITION`).
   - `infra/docker-compose.yml` — PostgreSQL 16 + Redis 7 (with password auth) for local development.
2. **Prisma schema (`apps/api/prisma/schema.prisma`)**:
   - Implement ALL models, relations, indexes, unique constraints, and enums from Section 9 of `architecture.md`:
     `User`, `BrokerAccount`, `TradingSettings`, `Strategy`, `BlackoutWindow`, `MarketSnapshot`, `TradeProposal`, `TradeApproval`, `TradeOrder`, `Position`, `TradeEvent`, `DailyRiskSnapshot`, `WeeklyRiskSnapshot`, `AuditLog`, `SystemEvent`.
   - Use `@db.Decimal` for all money, price, volume, and percentage fields, and UTC `DateTime` for timestamps.
   - Include a seed script creating the initial owner user (from env/dev defaults), default `TradingSettings` (DEMO mode, conservative risk limits from Section 7.1), and initial `xauusd_v1` `Strategy` record.
3. **API foundation**:
   - `GET /health` (unauthenticated liveness check, no sensitive info).
   - `GET /api/v1/system/health` (authenticated DB + Redis check).
   - Auth middleware (`requireAuth`, `requireOwner`) and internal worker auth middleware (`requireWorkerToken` validating `Authorization: Bearer <WORKER_AUTH_TOKEN>`).
4. **Configuration & hygiene**:
   - Root `.gitignore` (excluding `.env`, MT5 data, `node_modules`, `.venv`, build artifacts).
   - `.env.example` files matching Section 17 (including `CORS_ORIGIN`, `NEXT_PUBLIC_API_URL`, `MT5_ACCOUNT_TYPE=CENT`, `MT5_SYMBOL=XAUUSDc`, `MT5_MAGIC_NUMBER`).
   - Linting, typechecking, and test scripts across workspaces.

Do NOT implement MT5 execution or AI prompts yet. At the end, run typecheck, lint, Prisma validation/migration check, and unit tests for auth/health endpoints, and summarize what was created and how to boot locally.
```

### Exit Gate
- [ ] `docker compose up -d` starts PostgreSQL & Redis cleanly.
- [ ] Prisma migrations & seed succeed with zero schema errors.
- [ ] `apps/api` starts and `GET /health`, `/api/v1/auth/login`, and `/api/v1/auth/me` pass tests.
- [ ] `apps/web` boots and proxies `/api/health` cleanly.

---

## Phase 1 — Exness MT5 Connectivity & Account/Symbol Verification (Read-Only)

### Goal
Connect the Python worker safely to the local Windows Exness MT5 terminal (Standard or Standard Cent), enforce thread-safety, verify account/symbol specs, normalize broker server time to UTC, and sync read-only state to the Node.js API.

### Prompt

```markdown
Read `architecture.md` (Sections 2, 10.5, 11) and implement **Phase 1: MT5 Connectivity (Read-Only)**.

Build the following:
1. **Internal Worker API endpoints in `apps/api`** (protected by `requireWorkerToken`):
   - `GET /api/internal/v1/runtime-state` — returns active `TradingSettings`, active `Strategy`, active `BlackoutWindow`s, current `DailyRiskSnapshot`/`WeeklyRiskSnapshot`, pause and kill-switch state.
   - `POST /api/internal/v1/heartbeat` — updates worker health status in Redis/DB and publishes `system.health` to Redis Pub/Sub.
   - `POST /api/internal/v1/account/sync` — upserts `BrokerAccount` metadata (`login`, `server`, `accountCurrency`, `currencyScale`, `accountType`, `marginMode`, `magicNumber`, `mode`, live symbol specs) and updates latest balance/equity snapshot.
   - `POST /api/internal/v1/events` — persists `SystemEvent` records.
   - Public read endpoints: `GET /api/v1/account` and enhanced `GET /api/v1/system/health` (returning DB, Redis, worker heartbeat age, MT5 connected, Algo Trading flag).
2. **Python worker MT5 layer (`apps/worker/app/mt5/`)**:
   - `client.py` — Centralized `MT5Client` wrapping `MetaTrader5` with an in-process `threading.RLock()` (all MT5 calls must go through this lock), initialize/shutdown, and auto-reconnect logic.
   - `account.py` — Verify `login`, `server`, `trade_mode` (`DEMO` vs `REAL`), `margin_mode` (Hedging vs Netting), `account_info().trade_allowed`, `terminal_info().connected`, `terminal_info().trade_allowed` (Algo Trading button). Detect Standard (`USD`, `currencyScale=1`) vs Cent (`USC`, `currencyScale=100`). Refuse startup/trading on any mismatch.
   - `symbols.py` — Discover exact configured gold symbol (e.g., `XAUUSDc`, `XAUUSDm`, `XAUUSD`), ensure visibility in Market Watch, validate `trade_mode == SYMBOL_TRADE_MODE_FULL`, read `volume_min`, `volume_max`, `volume_step`, `trade_contract_size`, `trade_tick_size`, `trade_tick_value`, `point`, `digits`, `trade_stops_level`, `trade_freeze_level`, `filling_mode`, and dynamically resolve the supported filling mode (`FOK`/`IOC`/`RETURN`) from the `filling_mode` bitmask.
   - `market_data.py` — Detect MT5 broker server timezone offset and provide a tested helper converting MT5 epoch timestamps to true UTC `datetime`.
3. **Worker API client & heartbeat loop**:
   - `apps/worker/app/api_client.py` — Authenticated `httpx` client calling `/api/internal/v1/*` with timeouts and structured error handling.
   - `apps/worker/app/monitoring/health.py` & `logging.py` — Structured JSON logging with secret redaction + 10s heartbeat loop.
4. **Tests (`apps/worker/tests/`)**:
   - Unit tests mocking MT5 responses for Standard (`XAUUSD`, `USD`) and Cent (`XAUUSDc`, `USC`) accounts, `filling_mode` bitmask resolution, account mismatch rejection, Algo Trading disabled detection, and UTC timezone normalization.

Do NOT implement order placement yet. Run all Python and TypeScript tests and show exact verification commands for testing against a live Exness MT5 demo terminal on Windows.
```

### Exit Gate
- [ ] All unit tests for Standard/Cent verification, `filling_mode`, and UTC conversion pass.
- [ ] On Windows with Exness MT5 demo open, worker connects, syncs `BrokerAccount`, and `/api/v1/account` + `/api/v1/system/health` show accurate read-only data.

---

## Phase 2 — Market Data Pipeline & Real-Time SSE Streaming

### Goal
Stream live XAUUSD quotes and system health via Redis Pub/Sub → Node.js SSE (`/api/v1/stream`), persist closed candles, expose chart candle APIs, and build the live Market Overview UI and persistent status header.

### Prompt

```markdown
Read `architecture.md` (Sections 4, 10.1, 10.2, 10.5, 11, 12) and implement **Phase 2: Market Data & Real-Time SSE**.

Build the following:
1. **Worker market data & Redis Pub/Sub (`apps/worker/`)**:
   - `app/queue/pubsub.py` — Publish validated JSON events to `REDIS_PUBSUB_CHANNEL` (`quote.updated`, `system.health`, `risk.alert`).
   - `app/mt5/market_data.py` & `app/market/candles.py` — Fetch live bid/ask/spread ticks (throttled every 1–2s, skip unchanged quotes) and closed M5/M15/H1 candles with UTC normalization and stale-data detection.
   - Push closed candle snapshots to `POST /api/internal/v1/market/snapshot` and live ticks to Redis Pub/Sub.
2. **Backend SSE & Market API (`apps/api/`)**:
   - `POST /api/internal/v1/market/snapshot` — Persist `MarketSnapshot` (upsert by symbol/timeframe/candleTime) and publish updates.
   - `GET /api/v1/market/xauusd` — Latest quote, spread, staleness status, and indicator summary.
   - `GET /api/v1/market/xauusd/candles?timeframe=M15&limit=200` — Historical UTC candles for Lightweight Charts.
   - `GET /api/v1/stream` — Authenticated SSE endpoint subscribed to Redis Pub/Sub:
     - Set headers `Content-Type: text/event-stream`, `Cache-Control: no-cache, no-transform`, `Connection: keep-alive`, `X-Accel-Buffering: no`.
     - Enforce per-user max concurrent SSE connection limit.
     - Send `: ping\n\n` heartbeats every 20s and support `Last-Event-ID` / clean disconnect cleanup.
3. **Frontend (`apps/web/`)**:
   - Authenticated app shell with persistent status header displaying: **DEMO / LIVE** badge, **STANDARD / CENT** badge, **SSE connection indicator**, MT5/worker health dot, and placeholder **Kill Switch** button.
   - Reusable SSE client hook (`useTradingStream`) that updates live quote state and invalidates TanStack Query caches on reconnect/events.
   - **Login** page and **Market Overview** page featuring Lightweight Charts candlestick chart (M5/M15/H1 switcher), live SSE bid/ask/spread, and stale-data warning banner.
4. **Tests**:
   - API tests for `/api/v1/stream` auth enforcement, headers, heartbeat, and `/api/v1/market/xauusd/candles`.
   - Worker tests for candle UTC normalization, closed-candle detection, and quote throttling.

Run lint, typecheck, and all tests before finishing.
```

### Exit Gate
- [ ] Dashboard logs in, establishes SSE (`/api/v1/stream`), and updates live bid/ask/spread without page refresh.
- [ ] Lightweight Charts renders historical M5/M15/H1 XAUUSD candles cleanly.
- [ ] Disconnecting/restarting the API triggers clean SSE reconnect and cache refresh.

---

## Phase 3 — Indicators, Deterministic Strategy Engine, Blackout Windows & Backtester

### Goal
Implement reproducible technical indicators, the deterministic `xauusd_v1` rule-based strategy, deduplication/cooldown/rollover/blackout filters, Blackout Window API/UI, and the historical backtesting engine.

### Prompt

```markdown
Read `architecture.md` (Sections 6, 9, 10.4, 11, 15) and implement **Phase 3: Indicators, Deterministic Strategy, Blackout Windows & Backtesting**.

Build the following:
1. **Indicators & feature extraction (`apps/worker/app/market/`)**:
   - `indicators.py` — Compute `EMA 20`, `EMA 50`, `EMA 200`, `RSI 14`, `ATR 14` deterministically on closed candles (using pinned `pandas-ta` or `TA-Lib`).
   - `features.py` — Extract recent swing highs/lows, support/resistance reference levels, candle structure, live spread, session status, daily XAUUSD rollover break check (configurable UTC window), and active blackout window check.
2. **Deterministic Strategy Engine (`apps/worker/app/strategy/`)**:
   - `base.py` & `signal.py` — Typed Pydantic `SetupCandidate` model (`direction`, `symbol`, `timeframe`, `candle_time`, `entry_reference`, `stop_loss`, `take_profit`, `reward_risk`, `dedup_key`, `deterministic_reasons`, `invalidation_rules`).
   - `xauusd_v1.py` — Implement H1 EMA trend filter + M15 higher-low/lower-high continuation setup on **closed candles only**, structural SL with ATR buffer, deterministic R:R TP (`MARKET` orders only).
   - Enforce dedup key `(strategyId, symbol, timeframe, candleTime, direction)`, post-rejection/post-loss cooldown, session window, daily rollover exclusion, and active `BlackoutWindow` filtering.
3. **Blackout Windows & Strategy API (`apps/api/`)**:
   - `GET /api/v1/strategies` & `PATCH /api/v1/strategies/:id` (audited).
   - `GET /api/v1/blackouts`, `POST /api/v1/blackouts`, `DELETE /api/v1/blackouts/:id` (audited, Zod-validated).
   - Include active blackouts and strategy parameters in `GET /api/internal/v1/runtime-state`.
4. **Backtesting Engine (`apps/worker/backtest/`)**:
   - `data_loader.py` — Export/load historical MT5 candles (UTC) to/from local CSV/Parquet fixtures.
   - `engine.py` — Event-driven bar-by-bar replay calling the exact same `xauusd_v1` strategy and modeling spread, commission per lot, swap (including Wednesday 3x swap), slippage, and rollover exclusion.
   - `metrics.py` — Compute win rate, net P/L, profit factor, expectancy, max drawdown, average R, consecutive losses, and support out-of-sample / walk-forward splits.
5. **Tests**:
   - Fixture-based unit tests verifying indicator accuracy, reproducible long/short signals, no signals on unfinished candles, dedup/cooldown/rollover/blackout blocking, and backtest engine smoke run.

Run all unit tests and provide an example CLI command to run a backtest report on fixture data.
```

### Exit Gate
- [ ] Indicator and strategy unit tests pass 100% deterministically on fixtures.
- [ ] Backtest CLI runs end-to-end on sample XAUUSD candles and prints full metrics.
- [ ] Blackout windows and strategy parameters can be managed via API and read by the worker.

---

## Phase 4 — Deterministic Risk Engine (Standard & Cent Support)

### Goal
Build the independent, deterministic risk module that calculates position sizing using live MT5 functions in native currency (`USD` or `USC`), models broker costs, tracks equity-based daily/weekly drawdown, and enforces all mandatory pre-trade gates.

### Prompt

```markdown
Read `architecture.md` (Sections 7, 9, 10.4, 15) and implement **Phase 4: Deterministic Risk Engine**.

Build the following:
1. **Risk sizing & cost modeling (`apps/worker/app/risk/sizing.py`)**:
   - Calculate `risk_budget = account_equity * risk_percent` using `Decimal`.
   - Model `estimated_costs = spread_cost + (commission_per_lot * volume) + slippage_buffer`.
   - Use MT5 `order_calc_profit` and `order_calc_margin` in native account currency (`USC` for Standard Cent, `USD` for Standard) to find the largest valid volume aligned to `volume_step` within `[volume_min, volume_max]` such that:
     - `abs(order_calc_profit(...)) + estimated_costs <= risk_budget`
     - Free margin covers `order_calc_margin` with configurable safety buffer.
   - If even `volume_min` exceeds `risk_budget`, return `RISK_REJECTED` with clear reason (NEVER widen risk, tighten SL, or force `volume_min`).
2. **Drawdown & limit checks (`apps/worker/app/risk/limits.py`)**:
   - Equity-based daily and weekly drawdown formula: `((starting_equity - current_equity) / starting_equity) * 100`.
   - Detect `DEAL_TYPE_BALANCE` deposits/withdrawals from MT5 deal history and adjust baseline equity (with audit event) so deposits/withdrawals never distort P/L limits.
   - Enforce `dailyLossLimit` (auto-resets next broker day), `weeklyLossLimit` (stays tripped until manual review/reset), `maxPositions` (1), `maxPendingProposals` (3), `maxSpread`, `maxDeviationPoints`, `trade_stops_level`/`trade_freeze_level`, rollover, and blackout windows.
3. **Unified pre-trade validator (`apps/worker/app/risk/validator.py`)**:
   - Single reusable validation pipeline covering every bullet in Section 7.4 (used both before creating a proposal AND inside the execution lock before `order_send`).
4. **Backend Risk & Settings API (`apps/api/`)**:
   - `GET /api/v1/settings/trading` and `PATCH /api/v1/settings/trading` (strict Zod bounds preventing unsafe values like disabling SL or enabling martingale; writes `AuditLog`; publishes `settings.updated` to Redis Pub/Sub).
   - `POST /api/v1/risk/weekly/reset` (requires review `note`, resets tripped `WeeklyRiskSnapshot`, writes `AuditLog`).
   - Internal endpoint support for daily/weekly snapshot syncing.
5. **Comprehensive unit tests (`apps/worker/tests/test_risk.py`)**:
   - Test Standard `$100` account at `0.25%` risk rejecting a `$5` gold SL (`RISK_REJECTED`).
   - Test Standard Cent `10,000 USC` (`$100`) account at `0.25%–0.5%` risk sizing `XAUUSDc` accurately.
   - Test `volume_step` rounding, commission inclusion, stop-level violations, daily loss trip, weekly loss trip + manual reset, and `DEAL_TYPE_BALANCE` baseline adjustment.

Run all Python and TypeScript tests and verify zero failures.
```

### Exit Gate
- [ ] Standard `$100` over-budget trade is deterministically rejected (`RISK_REJECTED`).
- [ ] Cent (`XAUUSDc` / `USC`) sizing matches MT5 profit calculation formulas accurately.
- [ ] Weekly loss limit stays locked until `POST /api/v1/risk/weekly/reset` is called with an audit note.

---

## Phase 5 — AI Analysis Service (Commentary, Veto & Fallback)

### Goal
Integrate OpenAI structured outputs strictly as an explainability and advisory-veto layer triggered only on valid deterministic candidates, with timeout protection, fallback handling, and full audit logging.

### Prompt

```markdown
Read `architecture.md` (Section 6.3, 9, 11) and implement **Phase 5: AI Analysis Service**.

Build the following in `apps/worker/app/ai/`:
1. `schemas.py` — Strict Pydantic v2 schema for AI responses:
   - `recommendation`: `"PROCEED" | "VETO"`
   - `confidence`: float in `[0.0, 1.0]`
   - `market_regime`: non-empty string
   - `reasons`: list of concise strings
   - `risks`: list of concise strings
   - `invalidation_notes`: list of concise strings
   - Explicitly forbid any price, SL, TP, or volume fields from overriding deterministic strategy/risk outputs.
2. `prompts.py` — Versioned system/user prompt builder (`PROMPT_VERSION = "v1.0"`) passing only structured market features, closed-candle context, and the pre-computed deterministic candidate setup.
3. `client.py`:
   - Called **only** after the deterministic strategy engine produces a valid candidate AND initial risk validation passes.
   - Enforce `AI_TIMEOUT_SECONDS` (default `5s`) and OpenAI structured JSON output.
   - Capture `aiModel`, `aiPromptVersion`, `aiLatencyMs`, `aiTokensUsed`, and `aiRawResponse`.
   - Implement `AI_FALLBACK_MODE`:
     - `DETERMINISTIC_ONLY` (default): On timeout, HTTP error, rate limit, or schema failure, return `aiStatus = UNAVAILABLE` with deterministic rule summary so the proposal can still be reviewed with a clear warning banner.
     - `SKIP`: Drop candidate and emit a `SystemEvent`.
   - On `VETO`: Persist proposal as `AI_VETOED` (not `PENDING_APPROVAL`) for audit visibility.
4. **Tests (`apps/worker/tests/test_ai.py`)**:
   - Mock valid `PROCEED` response, `VETO` response, malformed JSON / out-of-range confidence, timeout fallback (`DETERMINISTIC_ONLY` vs `SKIP`), and verify AI is never called when strategy emits no signal.

Run all unit tests and verify zero failures.
```

### Exit Gate
- [ ] AI is invoked only when deterministic strategy triggers.
- [ ] Malformed AI JSON or timeout never crashes the worker and obeys `AI_FALLBACK_MODE`.
- [ ] AI cannot alter entry, SL, TP, or lot size.

---

## Phase 6 — Semi-Automatic Approval Workflow, Proposal Expiry & Kill Switch

### Goal
Connect proposal creation → SSE push → human review → single-use approval/rejection → Redis Stream command enqueue, along with proposal expiry and the emergency Kill Switch.

### Prompt

```markdown
Read `architecture.md` (Sections 8, 9, 10.3–10.5, 11, 12) and implement **Phase 6: Approval Workflow & Kill Switch**.

Build the following:
1. **Backend Proposal, Control & Command Queue API (`apps/api/`)**:
   - `POST /api/internal/v1/proposals` — Persist `TradeProposal` (enforcing unique `dedupKey` and `maxPendingProposals`), publish `proposal.created` over SSE.
   - `POST /api/internal/v1/proposals/expire` — Transition overdue `PENDING_APPROVAL` proposals to `EXPIRED`, publish `proposal.expired` over SSE.
   - `POST /api/internal/v1/proposals/:id/claim` — Atomic conditional update `APPROVED → EXECUTING` (`WHERE status='APPROVED' AND expiresAt > NOW()`), returning `409 Conflict` if already expired, cancelled, or claimed.
   - Public endpoints (auth + CSRF + rate limit):
     - `GET /api/v1/proposals` (filter by status/date, paginated) and `GET /api/v1/proposals/:id`.
     - `POST /api/v1/proposals/:id/approve` — Requires unique `idempotencyKey`. Validates proposal is `PENDING_APPROVAL`, not expired, system not paused, kill switch off. Creates `TradeApproval`, updates proposal to `APPROVED`, writes `AuditLog`, appends `EXECUTE_PROPOSAL` command to Redis Stream (`commands:trading`), and publishes SSE `proposal.updated`.
     - `POST /api/v1/proposals/:id/reject` — Creates `TradeApproval(REJECT)`, marks `REJECTED`, triggers cooldown timestamp, writes `AuditLog`, publishes SSE update.
     - `POST /api/v1/proposals/:id/cancel` — Cancels `PENDING_APPROVAL` proposal, writes `AuditLog`.
   - System controls:
     - `POST /api/v1/system/kill-switch` — Sets DB `killSwitchActive = true` AND Redis `system:kill_switch = 1`, cancels all `PENDING_APPROVAL` proposals, writes `AuditLog`, broadcasts SSE `system.health` + `risk.alert`.
     - `POST /api/v1/system/pause` and `POST /api/v1/system/resume` (resume verifies health/risk gates first).
2. **Worker market-scan & expiry jobs (`apps/worker/app/jobs/`)**:
   - `market_scan.py` — End-to-end closed-candle pipeline (indicators → strategy → risk check → AI commentary → `POST /api/internal/v1/proposals`).
   - `proposal_expiry.py` — Periodic 5–10s check triggering expiry.
3. **Frontend Approval UI (`apps/web/`)**:
   - **AI Proposals** list page (live SSE updates + optional browser Web Notification & audio chime on new proposal).
   - **Proposal Detail** page showing every required field from Section 8.2 (DEMO/LIVE & STANDARD/CENT badges, native currency + USD equivalent max loss, live SSE bid/ask/spread, AI explanation or `AI Unavailable` badge, live expiry countdown, Approve & Reject buttons with double-click protection and generated `idempotencyKey`).
   - Wire up the header **Kill Switch** button with confirmation dialog.
4. **Tests**:
   - API tests for single-use approval, duplicate `idempotencyKey` protection, expired proposal rejection, kill switch canceling pending proposals, and atomic `/claim` race protection.

Run lint, typecheck, and all API/worker tests.
```

### Exit Gate
- [ ] New proposals appear immediately on the dashboard via SSE with live countdown.
- [ ] Double-clicking Approve or reusing an `idempotencyKey` never produces duplicate Redis Stream commands.
- [ ] Activating Kill Switch sets Redis + DB flags, cancels pending proposals, and blocks new approvals immediately.

---

## Phase 7 — Demo Execution, Manual Close & Stuck-State Reconciliation

### Goal
Implement the Python Redis Streams consumer, distributed execution lock, MT5 `order_check` + `order_send` (open & manual close), position/deal sync, and crash/timeout stuck-state recovery.

### Prompt

```markdown
Read `architecture.md` (Sections 8.4, 8.5, 10.2, 10.5, 11, 15) and implement **Phase 7: Demo Execution, Manual Close & Reconciliation**.

Build the following:
1. **Backend Manual Close & Internal Sync Endpoints (`apps/api/`)**:
   - `POST /api/v1/positions/:id/close` — Authenticated, CSRF-protected, requires `idempotencyKey`. Allowed even when paused, loss-limited, or Kill Switch is active (risk-reducing action). Verifies position is `OPEN` (not already `CLOSING`/`CLOSED`), transitions `OPEN → CLOSING` with `closeRequestedAt`, writes `AuditLog`, enqueues `CLOSE_POSITION` command to Redis Stream (`commands:trading`), and publishes SSE update.
   - `GET /api/v1/positions` and `GET /api/v1/orders` (trade history with retcodes, fees, commissions, swaps).
   - `GET /api/internal/v1/reconciliation/stuck` — Returns proposals in `EXECUTING` or positions in `CLOSING` older than 45s.
   - `POST /api/internal/v1/orders/result` — Persists `TradeOrder` (`OPEN` or `CLOSE`), updates `TradeProposal` / `Position`, appends immutable `TradeEvent`, publishes SSE `order.updated` / `position.closed`.
   - `POST /api/internal/v1/positions/sync` — Syncs open/closed positions, live floating P/L, commission, swap, and flags `isBotManaged` based on `magicNumber`.
2. **Worker Redis Stream Consumer, Locks & MT5 Execution (`apps/worker/`)**:
   - `app/queue/locks.py` — Redis per-account distributed lock (`lock:execution:<accountLogin>`, 30s TTL, safe Lua release) + fast Redis kill-switch check (`system:kill_switch`).
   - `app/queue/streams.py` & `app/jobs/command_consumer.py` — `XREADGROUP` consumer for `EXECUTE_PROPOSAL` and `CLOSE_POSITION`, `XACK` only after reconciliation + API persistence, poison-message routing to `commands:trading:dlq`.
   - `app/mt5/execution.py`:
     - **Open flow:** Follow all 12 steps in Section 11 (lock → kill-switch check → atomic `/claim` → MT5/account check → refresh quote/specs/positions → full risk/deviation revalidation → `order_check` → single `order_send` with resolved `filling_mode`, `deviation`, `magic`, `comment="AGY:<shortId>"`, mandatory `sl`, `tp` → reconcile deal/position → persist via API → SSE → `XACK`).
     - **Manual close flow:** Follow all 8 steps in Section 11 (lock → verify position still open on MT5 → fresh tick + `filling_mode` → opposite `TRADE_ACTION_DEAL` referencing `position=brokerPositionId` → `order_check` → single `order_send` → reconcile closing deal price/profit/commission/swap → persist → SSE → `XACK`).
     - **Stuck-state recovery:** On startup (before consuming new commands) and every 30–60s, query `/api/internal/v1/reconciliation/stuck` and reconcile against MT5 `positions_get()`, `orders_get()`, and `history_deals_get()` by `magic`/`comment`/`brokerPositionId`. Mark `UNKNOWN` and fail closed if ambiguous—never blindly retry `order_send`.
   - `app/jobs/position_sync.py` — Sync open positions & closed deals every 2–5s and publish SSE `position.updated` events.
3. **Tests**:
   - Unit & integration tests (mocked MT5 + real Redis/DB) covering:
     - Successful open + reconciliation, price deviation abort, kill-switch abort inside lock, and atomic claim 409 handling.
     - Successful manual close (even while Kill Switch is ON) + duplicate close idempotency protection.
     - Timeout/crash stuck-state recovery (order found on MT5 → reconciled without duplicate; order absent → marked failed; MT5 disconnected → `UNKNOWN` + fail-closed).

Run all Python and TypeScript tests before finishing.
```

### Exit Gate
- [ ] End-to-end demo approval places a single MT5 demo order with valid `filling_mode`, `magic`, `comment`, SL, and TP.
- [ ] Clicking Manual Close closes the demo position on MT5, reconciles commission/swap/profit, and updates the UI via SSE.
- [ ] Simulated timeout after `order_send` reconciles cleanly with zero duplicate trades.

---

## Phase 8 — Complete Dashboard, Reports, Settings, Health & Audit Logs

### Goal
Complete all 11 frontend pages and remaining backend reporting/audit endpoints so the entire trading lifecycle is observable and controllable from the web UI.

### Prompt

```markdown
Read `architecture.md` (Sections 10, 12, 14) and implement **Phase 8: Complete Dashboard, Reports & Observability UI**.

Build the following:
1. **Backend Reporting & Audit Endpoints (`apps/api/`)**:
   - `GET /api/v1/dashboard/summary` — Balance, equity, native currency + USD equivalent (`currencyScale`), daily/weekly drawdown progress, open position count, pending proposal count, pause/kill-switch state.
   - `GET /api/v1/reports/performance` — Win rate, net P/L (native + USD equivalent), profit factor, expectancy, max drawdown, average R, total commissions & swaps, filterable by date range.
   - `GET /api/v1/audit-logs` — Paginated, filterable immutable audit log feed.
2. **Frontend Pages (`apps/web/`)** — Complete and polish all 11 pages from Section 12:
   - **Dashboard** — Account KPIs (native + USD equivalent for Cent), daily/weekly loss progress bars, active proposals card, open position summary, quick Pause/Resume & Kill Switch controls.
   - **Open Positions** — Live SSE floating P/L, SL/TP, bot-managed vs manual badge, **Close Position** button with confirmation modal (showing live bid/ask, spread, floating P/L, and idempotency key).
   - **Trade History** — Orders, deals, open/close action types, retcodes, commissions, swaps, realized P/L.
   - **Risk & Strategy Settings** — Edit `TradingSettings` (risk %, daily/weekly limits, max spread, deviation, commission per lot, cooldown, rollover, AI fallback), **Weekly Loss Limit Manual Reset** modal (with required review note), Strategy toggle/params, and **News Blackout Windows** CRUD table.
   - **Reports** — Performance summary cards, equity curve chart, R-multiple & drawdown breakdown.
   - **System Health** — MT5 connection, Algo Trading flag, account/symbol verification summary, worker heartbeat age, Redis stream lag, SSE status, DB health.
   - **Audit Logs** — Searchable timeline of approvals, rejections, manual closes, settings edits, weekly resets, pauses, kill switches, and execution errors.
3. **SSE Alerts & Notifications**:
   - Toast notifications + optional browser Web Notification / audio alert for all events in Section 14 (new proposal, expiry warning, fill/rejection, manual close, loss limit reached, MT5 disconnect, kill switch).

Run frontend build, lint, typecheck, and API tests.
```

### Exit Gate
- [ ] All 11 pages render with real data, strict DEMO/LIVE & STANDARD/CENT badges, and zero TypeScript/build errors.
- [ ] Native (`USC`) and USD equivalent displays are accurate everywhere on Cent demo accounts.
- [ ] Every state-changing UI action produces a visible `AuditLog` entry.

---

## Phase 9 — Demo Forward Testing, Failure Drills & Windows Deployment Hardening

### Goal
Harden deployment for 24/5 Windows operation, verify backups/restores, and execute every failure drill before considering live mode.

### Prompt

```markdown
Read `architecture.md` (Sections 2.4, 13, 15, 16 Phase 9) and implement **Phase 9: Failure Drills & Production Hardening**.

Build and execute the following:
1. **Windows supervision scripts (`infra/windows/`)**:
   - NSSM Windows Service or Task Scheduler setup script to auto-start and restart `apps/worker` on crash/reboot.
   - Startup checklist & script for Exness MT5 terminal auto-start and Algo Trading verification.
   - Documentation for storing MT5 credentials in Windows Credential Manager (never plaintext `.env`).
2. **Nginx & TLS template (`infra/nginx/`)**:
   - Reverse proxy config serving `apps/web` and forwarding `/api/*` to `apps/api` with TLS headers, strict rate limiting on auth/approval/close, and `proxy_buffering off` + long read timeout on `/api/v1/stream`.
3. **Database backup & restore scripts (`infra/scripts/`)**:
   - Automated `pg_dump` backup script with retention and a documented restore verification script.
4. **Automated failure-drill test suite**:
   - Drill 1: MT5 terminal disconnect mid-run → fail-closed, heartbeat alert, clean reconnect.
   - Drill 2: Algo Trading disabled in MT5 → pre-trade check rejects cleanly.
   - Drill 3: Simulated `order_send` timeout → stuck-state reconciliation prevents duplicate order.
   - Drill 4: Kill Switch triggered while `APPROVED` job is queued → open aborted, existing open position still manually closeable.
   - Drill 5: OpenAI timeout / 500 error → `DETERMINISTIC_ONLY` fallback works with UI warning badge.
   - Drill 6: Double-click Approve and double-click Manual Close → exactly one command/order.
   - Drill 7: SSE drop & reconnect → heartbeat resumes and TanStack Query refreshes missed state.
5. **Demo Forward-Test Sign-Off Template (`docs/demo-signoff-checklist.md`)**:
   - Structured checklist matching all 5 Release Gates in Section 15.

Run the full test suite (API + Worker + Web build) and summarize drill results.
```

### Exit Gate
- [ ] All 7 failure drills pass and are repeatable via automated tests or documented scripts.
- [ ] PostgreSQL backup & restore verified on a test database.
- [ ] Windows supervision scripts and Nginx SSE proxy config reviewed and ready.

---

## Phase 10 — Live Mode Dual-Lock & Limited Live Pilot Gate

### Goal
Implement the 3-way Live Mode Dual-Lock and perform a strict pre-live security & risk audit before unlocking a small Cent live pilot.

### Prompt

```markdown
Read `architecture.md` (Sections 13, 15, 16 Phase 10, 18) and implement **Phase 10: Live Mode Dual-Lock & Pre-Live Audit**.

Build and verify the following:
1. **Live Mode Dual-Lock enforcement**:
   - Worker refuses to execute on a real account unless ALL THREE are true:
     1. Worker env `MT5_ACCOUNT_MODE=LIVE`.
     2. MT5 `account_info().trade_mode == ACCOUNT_TRADE_MODE_REAL`.
     3. `BrokerAccount` in DB has `mode = LIVE` with non-null `liveUnlockedAt` and `liveUnlockedBy` recorded in `AuditLog`.
   - Any mismatch (e.g., `MT5_ACCOUNT_MODE=DEMO` connected to a real login, or `LIVE` env without DB unlock) must immediately fail closed and log a `CRITICAL` `SystemEvent`.
   - Add an audited owner-only unlock endpoint (`POST /api/v1/account/unlock-live`) requiring explicit typed confirmation + re-authentication, plus an instant lock-back-to-demo toggle.
2. **Pre-live security & DoD audit**:
   - Audit every endpoint for authentication, CSRF, Zod validation, and rate limiting.
   - Verify secret redaction in both Node.js and Python logs.
   - Verify no browser endpoint can submit arbitrary new MT5 order parameters.
   - Verify every item in Section 18 (Definition of Done for Version 1) against the codebase and report any remaining gaps.
3. **Tests**:
   - Unit/integration tests for every Live Mode Dual-Lock mismatch combination proving fail-closed behavior.

Do NOT switch `.env` to LIVE automatically. Leave the repository default posture strictly in `DEMO` mode.
```

### Exit Gate
- [ ] All Live Mode Dual-Lock mismatch tests fail closed.
- [ ] All Section 15 Release Gates and Section 18 Definition of Done criteria are signed off.
- [ ] System remains in `DEMO` mode by default until the owner explicitly completes the demo test period and unlocks a limited live pilot (Standard Cent recommended for USD 100–500).

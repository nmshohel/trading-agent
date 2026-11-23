# Personal AI Forex Trading Agent — Architecture

**Project:** Personal AI Forex Trading Agent  
**Broker:** Exness  
**Trading platform:** MetaTrader 5 (MT5)  
**Instrument:** XAUUSD (Standard or Cent, e.g., `XAUUSD`, `XAUUSDm`, `XAUUSDc`)  
**Trading mode:** Semi-Automatic (human approval required for every entry; manual close supported)  
**Initial capital range:** USD 100–500 (Standard Cent account recommended for small capital)  
**Architecture version:** 1.2  
**Status:** Development specification — demo-first

---

## 1. Project Overview

Build a personal trading assistant that monitors XAUUSD, analyzes market conditions, creates explainable trade proposals, and waits for the account owner to approve or reject each proposal. Only after explicit approval may the execution service submit a new order to the connected Exness MT5 account.

The system is an **AI-assisted decision-support and execution application**, not a profit-guaranteeing system. It must prioritize capital preservation, deterministic risk checks, auditability, and safe failure behavior.

### 1.1 Primary goals

- Connect to one Exness MT5 trading account (Standard or Standard Cent).
- Read account state, XAUUSD prices, candles, live symbol specifications, and open positions.
- Calculate technical indicators and generate structured, rule-based trade setups.
- Use AI to summarize market context, explain invalidation conditions, and optionally flag risks—without inventing prices or overriding risk rules.
- Stream live quotes, proposals, execution status, open position P/L, and alerts to the dashboard via Server-Sent Events (SSE).
- Present each proposal for manual approval.
- Revalidate price, spread, account state, kill switch, and risk immediately before execution.
- Submit a market order with mandatory Stop Loss and Take Profit according to the active strategy.
- Allow the owner to manually close an open position from the dashboard with full auditability.
- Monitor open positions and record execution outcomes, audit logs, and performance reports.

### 1.2 Explicit non-goals for version 1

- No fully autonomous order execution.
- No arbitrary manual **new** order creation from the browser (only approved proposals may open trades; manual close is risk-reducing only).
- No automated trailing stop, partial close, or SL/TP modification workflows in v1 (may be added in v2).
- No broker pending orders (`BUY_LIMIT`, `SELL_STOP`, etc.) in v1 — `MARKET` entry orders only.
- No deposits, withdrawals, or fund transfers.
- No multi-broker or multi-account support.
- No copy trading or signal selling.
- No martingale, grid, averaging-down, or uncontrolled position stacking.
- No promise of positive returns.
- No AI-generated direct broker commands.

---

## 2. Critical Broker and Platform Decisions

### 2.1 MT5 connection

Use the official MetaTrader 5 desktop terminal and its Python integration (`MetaTrader5` package) for the first release.

**Important:** Exness documents a separate Exness API, but that API is not compatible with standard MetaTrader-platform accounts. For this MT5-based project, use the MT5 terminal integration unless the account/platform arrangement is deliberately changed later.

The MT5 Python integration communicates with a locally installed and logged-in terminal. Therefore, the trading worker must run on a Windows machine or Windows VPS where the Exness MT5 terminal is installed, signed in, and kept available.

### 2.2 Account, Cent-account, and symbol verification

Do not assume the symbol is literally `XAUUSD` or that the currency is `USD`. Depending on account type, Exness exposes suffixes and currencies such as:

- Standard / Pro / Raw Spread / Zero: `XAUUSD`, `XAUUSDm`, `XAUUSDr`, etc. (typically `USD`, contract size `100` troy oz).
- **Standard Cent:** `XAUUSDc` (currency `USC`, where `100 USC = 1 USD`, allowing fine-grained position sizing for USD 100–500 accounts).

At startup and before execution, the connector must verify:

1. Connected account `login`, `server`, `currency`, `balance`, `equity`, `trade_mode` (`DEMO` vs `REAL`), `margin_mode` (Hedging vs Netting), and `account_info().trade_allowed`.
2. Terminal status: `terminal_info().connected` and `terminal_info().trade_allowed` (ensuring the MT5 "Algo Trading" button is enabled).
3. Connected account matches the configured account and expected mode (`DEMO` unless live mode is explicitly unlocked).
4. Exact configured gold symbol exists, is visible in Market Watch, and is tradeable (`trade_mode == SYMBOL_TRADE_MODE_FULL`).
5. Live symbol specs: `volume_min`, `volume_max`, `volume_step`, `trade_contract_size`, `trade_tick_size`, `trade_tick_value`, `point`, `digits`, `trade_stops_level`, `trade_freeze_level`, `filling_mode`, and `expiration_mode`.
6. Resolve a broker-supported order filling mode dynamically from the `filling_mode` bitmask (`ORDER_FILLING_FOK`, `ORDER_FILLING_IOC`, or `ORDER_FILLING_RETURN`) instead of hardcoding one.
7. Refuse trading if any required value is unavailable, zero, or inconsistent.

**Cent account handling (`USC`):**

- All risk calculations, `order_calc_profit`, and order requests run natively in the broker account currency (`USC` for Cent, `USD` for Standard).
- Store a `currencyScale` on `BrokerAccount` (e.g., `1` for `USD`, `100` for `USC`) so the dashboard can display both native account units (e.g., `25.00 USC`) and USD equivalent (`$0.25`) without ever corrupting stored broker amounts.
- Never hardcode contract size, lot size, tick value, spread, stop distance, leverage, or margin assumptions.

### 2.3 Order tagging, manual-trade isolation, and timezone normalization

- **Magic number & comment tagging:** Every order sent by the worker must include a configured `MT5_MAGIC_NUMBER` and a deterministic short `comment` (e.g., `AGY:<proposalShortId>`). This isolates bot-managed positions from any manual trades placed in the MT5 app and makes post-timeout order/deal reconciliation reliable.
- **Hedging vs Netting awareness:** Record `margin_mode` on startup. Version 1 targets standard Exness Hedging accounts (`ACCOUNT_MARGIN_MODE_RETAIL_HEDGING`), where each position has its own `brokerPositionId`. If a Netting account is detected, enforce strict single-position checks and reject unexpected volume aggregation.
- **MT5 server time to UTC:** The `MetaTrader5` Python API returns candle and tick epoch timestamps shifted to broker server time. The connector must normalize all persisted timestamps to true UTC and document the detected server offset in startup logs.
- **Daily rollover break:** XAUUSD has a daily ~1-hour market break (around 21:00–22:00 UTC or 22:00–23:00 UTC depending on DST) with wide spreads and halted quotes. The worker must treat rollover windows as non-tradeable.

### 2.4 Deployment and Windows supervision constraints

The MT5 Python package communicates with the desktop terminal on Windows. Do not deploy that connector as a Linux-only serverless function.

Recommended deployment:

- **Next.js dashboard:** Web host or VPS behind TLS.
- **Node.js API and PostgreSQL:** Linux VPS (or same private VPS).
- **Redis:** Same private VPS/network or managed Redis with private access and auth.
- **Python trading worker + Exness MT5 terminal:** Windows VPS or dedicated Windows PC.

**Windows reliability requirements:**

- Run the Python worker under a process supervisor (e.g., NSSM Windows Service or Task Scheduler with restart-on-failure).
- Configure MT5 terminal auto-start on boot and disable disruptive automatic Windows reboots during trading hours.
- Monitor worker heartbeat and MT5 connection continuously; fail closed on disconnect.

---

## 3. User Workflow

1. User signs in to the private dashboard; the browser opens an authenticated **SSE stream** (`/api/v1/stream`) for real-time updates.
2. Trading worker connects to MT5, verifies account/symbol specs (Standard or Cent), and reports health to the Node.js API.
3. Market-data service collects XAUUSD candles and current bid/ask, normalizing timestamps to UTC and pushing snapshots to the API/Redis Pub/Sub.
4. Strategy engine evaluates completed candles on configured timeframes and indicators.
5. **Only when a deterministic setup triggers**, the AI analysis service receives structured features + candidate parameters and returns validated commentary (or an advisory veto).
6. Deterministic risk engine sizes the trade using live MT5 specs and checks all limits (including daily/weekly loss limits, spread, margin, rollover, and blackout windows).
7. A valid proposal is persisted via the internal API as **Pending Approval** and pushed immediately to the dashboard via SSE (with optional browser notification/sound).
8. User reviews direction, entry, Stop Loss, Take Profit, calculated volume, estimated loss (native currency + USD equivalent), spread, rationale, risks, and expiry countdown.
9. User selects **Approve** or **Reject**.
10. On approval, the API validates session/CSRF/idempotency, marks the proposal `APPROVED`, and publishes an `EXECUTE_PROPOSAL` command to the Redis Stream.
11. Execution worker acquires a per-account Redis lock, verifies kill switch is off, re-fetches live quotes/account/positions, re-runs all risk checks, runs MT5 `order_check`, and calls `order_send` once with `magic`, `comment`, valid `filling_mode`, and `deviation`.
12. Worker reconciles the resulting order/deal/position from MT5, updates the API, and publishes an SSE event so the dashboard updates immediately.
13. Position monitor tracks open positions and streams floating P/L. At any time, the user can click **Close Position** on an open trade to send an audited `CLOSE_POSITION` command, or let broker-side SL/TP close it.

**Approval is not an order guarantee.** If the quote moves beyond allowed deviation, the proposal expires, the kill switch is engaged, risk changes, or the broker rejects the request, the order must not be forced through.

---

## 4. System Architecture

**Single Database Owner Pattern:** The Node.js API (via Prisma) is the **sole writer/reader of PostgreSQL**. The Python trading worker does not connect to PostgreSQL directly; instead, it pushes state/events to authenticated internal Node.js API endpoints and consumes execution/close commands from Redis Streams. Real-time UI updates flow from Redis Pub/Sub through the Node.js API to the browser over SSE.

```mermaid
flowchart TD
    U["User (Browser)"] --> UI["Next.js Dashboard"]
    UI -- "REST + SSE (/api/v1/stream)" --> API["Node.js / Express API"]
    API -- "Prisma ORM" --> DB[("PostgreSQL")]
    API -- "Commands (Redis Streams) & Locks" --> R[("Redis")]
    R -- "Pub/Sub Events" --> API
    R -- "Consume EXECUTE / CLOSE Commands" --> W["Python Trading Worker"]
    W -- "Internal Auth HTTP (/api/internal/v1/*)" --> API
    W -- "Publish Live Quotes & Events" --> R
    W --> MT5["Exness MT5 Desktop Terminal"]
    MT5 --> EX["Exness Trading Server"]
    W --> MD["Market Data & Indicators"]
    MD --> ST["Deterministic Strategy Engine"]
    ST --> AI["AI Analysis Service (Commentary / Veto)"]
    AI --> RV["Deterministic Risk Validator"]
    RV --> W
```

### 4.1 Components

| Component | Responsibility | Technology |
| --- | --- | --- |
| Dashboard | Setup review, approval, manual close, charts, positions, reports, controls | Next.js (App Router), TypeScript, Tailwind CSS, shadcn/ui, SSE (`EventSource`) |
| API | Auth, authorization, REST endpoints, SSE fan-out, internal worker ingestion, sole DB owner | Node.js, Express, TypeScript, Zod, Prisma |
| Database | Users, broker metadata, settings, proposals, approvals, orders, positions, risk snapshots, audit logs | PostgreSQL, Prisma ORM |
| Redis | Command queue (**Redis Streams**), live event fan-out (**Pub/Sub**), distributed execution lock, kill-switch fast flag | Redis (`ioredis` in Node, `redis-py` in Python) |
| MT5 connector | Terminal connection, account/symbol verification, quotes/candles, `order_check`, `order_send`, position close | Python, `MetaTrader5` |
| Market analysis | UTC candle normalization, technical indicators, spread/session/rollover checks | Python, pandas, NumPy, `pandas-ta` or TA-Lib |
| Strategy engine | Deterministic rule-based setup detection, SL/TP calculation, deduplication | Python |
| AI analysis | Structured explanation, risk commentary, optional advisory veto (never chooses prices/lots) | OpenAI API (structured outputs) + deterministic fallback |
| Risk engine | Position sizing (Standard & Cent), broker cost modeling, daily/weekly loss limits, trade eligibility | Python deterministic module |
| Backtest engine | Historical strategy replay with spread, commission, swap, and slippage | Python (`backtest/` module) |
| Monitoring | Heartbeats, structured logs, health endpoints, SSE/browser alerts | Structured JSON logs, `/health`, `/api/v1/system/health` |

### 4.2 Trust boundaries

- The browser must never connect directly to MT5, Redis, or PostgreSQL.
- The browser receives live updates only through authenticated SSE (`/api/v1/stream`).
- The AI service must never hold broker credentials, choose lot sizes, invent SL/TP levels, or call MT5 execution.
- Only the Python execution worker may call MT5 `order_send`.
- The Node.js API is the single owner of PostgreSQL schema and writes; worker writes go through validated, token-authenticated internal API endpoints over a private network/TLS.
- The risk engine must be deterministic and independent of the language model.
- Every new order must link to a single-use user approval record; every manual close must link to an authenticated user request and audit record.

---

## 5. Recommended Technology Stack & Repository Layout

### Frontend

- Next.js (App Router), TypeScript, Tailwind CSS, shadcn/ui
- TanStack Query (initial data & cache invalidation driven by SSE events)
- Native `EventSource` (or reconnectable SSE wrapper) for real-time updates
- React Hook Form + Zod
- Lightweight Charts for XAUUSD price charts

### Backend (Node.js API)

- Node.js, Express, TypeScript
- Zod request validation
- Better Auth or secure HTTP-only cookie session authentication (with CSRF protection)
- Prisma ORM + PostgreSQL
- `ioredis` (Redis Streams producer, Pub/Sub subscriber for SSE, kill-switch flag)

### Trading worker (Python)

- Python 3.x version compatible with the pinned `MetaTrader5` package
- `MetaTrader5` Python package
- `pandas`, `NumPy`, `pandas-ta` or `TA-Lib` (choose one and pin versions)
- `Pydantic` v2 for strict schema validation (market data, AI output, queue payloads)
- `redis` (`redis-py`) for Redis Streams consumer groups, Pub/Sub publishing, and distributed execution locks (avoids Node-BullMQ ↔ Python protocol incompatibility)
- `httpx` for authenticated internal API calls with timeouts and retries (non-execution reads/ingests only)
- `pytest` for unit, risk, and backtest tests

### Monorepo folder layout

```text
trading-agent/
├── apps/
│   ├── web/                  # Next.js dashboard (App Router)
│   ├── api/                  # Node.js / Express / Prisma API + SSE server
│   └── worker/               # Python MT5 trading worker & backtest engine
├── packages/
│   └── shared/               # Shared JSON schemas, event types, constants
├── infra/
│   ├── docker-compose.yml    # PostgreSQL, Redis, API, Web (dev/Linux)
│   └── windows/              # NSSM / Task Scheduler scripts for worker + MT5
├── docs/
│   └── architecture.md
└── README.md
```

Pin dependency versions and test upgrades on demo before live use.

---

## 6. Trading Strategy Design

### 6.1 Version 1 strategy scope

Start with one transparent, rule-based strategy operating on **`MARKET` orders only**. The deterministic strategy engine computes direction, entry reference, Stop Loss, and Take Profit. AI only contextualizes and explains the setup (or issues an advisory veto).

Suggested initial analysis:

- **Higher timeframe:** H1 trend context.
- **Setup timeframe:** M15.
- **Optional entry confirmation:** M5.
- **Indicators:** EMA 20, EMA 50, EMA 200, RSI 14, ATR 14.
- **Context:** Recent swing high/low, support/resistance zones, candle structure, live spread, session status, rollover break, and blackout windows.

These are initial research parameters, not proven profitable settings. They must be backtested and tuned without overfitting.

### 6.2 Example setup logic & trade filters

A possible long candidate:

- H1 trend filter is bullish according to the selected EMA rule.
- M15 price structure supports a higher-low / bullish continuation setup.
- RSI confirms momentum without being used as a standalone signal.
- Entry trigger is confirmed by a **closed candle**, never an unfinished candle.
- SL is placed by the deterministic strategy at a structural invalidation point with an ATR-based safety buffer.
- TP is calculated deterministically from the configured reward/risk target.

A possible short candidate mirrors the conditions.

**Do not generate or execute a proposal if:**

- Candles or quotes are stale or incomplete.
- Spread exceeds `maxSpread`.
- Stop distance is invalid or smaller than broker `trade_stops_level` / `trade_freeze_level`.
- Current UTC time is inside the daily XAUUSD rollover break or outside configured session hours.
- Current UTC time falls inside an active **News Blackout Window** (also protecting against Exness High Margin Requirements around high-impact news and weekend open/close).
- The market is closed, trading is paused, kill switch is active, or daily/weekly loss limits are tripped.
- An open position or pending proposal violates exposure limits.
- **Deduplication / Cooldown:** A proposal for the same `(strategyId, symbol, timeframe, candleTime, direction)` already exists, or the post-rejection / post-loss cooldown timer is still active.

### 6.3 AI role, output contract, and fallback behavior

- **Trigger rule:** Call the AI service **only** after the deterministic strategy detects a valid setup candidate (never on every market tick or quiet candle). This controls API cost and latency.
- **Strict boundary:** The strategy engine supplies exact `direction`, `entry_reference`, `stop_loss`, and `take_profit`. The AI **must not** modify prices or lot sizes; it may only provide structured commentary and an advisory recommendation (`PROCEED` or `VETO`).

Validated JSON schema returned by AI:

```json
{
  "recommendation": "PROCEED",
  "confidence": 0.72,
  "market_regime": "BULLISH_CONTINUATION",
  "reasons": ["H1 EMA alignment bullish", "M15 higher-low bounce near EMA20"],
  "risks": ["Approaching prior session high", "Spread slightly elevated"],
  "invalidation_notes": ["Break below M15 swing low invalidates setup"]
}
```

- **Schema enforcement:** Reject missing, malformed, or out-of-range fields (`confidence` outside `0.0–1.0`, unsupported enum values).
- **Veto behavior:** If AI returns `VETO`, either suppress the proposal (logging it as `AI_VETOED` for review) or surface it clearly flagged as vetoed depending on config (default: log as `AI_VETOED` and do not prompt for approval).
- **Fallback on AI failure/timeout:** Enforce a strict timeout (e.g., `5` seconds). If the AI provider times out, errors, or rate-limits, obey `aiFallbackMode`:
  - `DETERMINISTIC_ONLY` (default): Still create the proposal with `aiStatus = UNAVAILABLE` and deterministic rule summary, displaying a prominent "AI explanation unavailable" badge on the approval screen.
  - `SKIP`: Drop the setup and log a `SystemEvent`.
- **Auditability:** Persist `aiModel`, `aiPromptVersion`, `aiLatencyMs`, `aiTokensUsed`, and `aiRawResponse` on the proposal record.

---

## 7. Risk Management — Mandatory

The initial capital range (USD 100–500) is small relative to standard gold contract exposure. The system must always reject trades when the broker's minimum volume exceeds the risk budget.

### 7.1 Initial demo configuration

| Setting | Initial value | Notes |
| --- | --- | --- |
| Mode | `DEMO` | Live execution disabled by default (dual-lock required) |
| Risk per trade | `0.25%` (configurable up to `0.5%`) | Calculated from current equity |
| Daily loss limit | `1.0%` | Blocks new proposals/executions until next broker day |
| Weekly loss limit | `2.0%` | Blocks new proposals/executions; **requires manual review & reset** |
| Maximum open XAUUSD positions | `1` | Version 1 |
| Maximum pending proposals | `3` | Expire old proposals automatically |
| Minimum reward/risk | `1:2` | Strategy setting, not a guarantee |
| Order type | `MARKET` only | No broker pending orders in v1 |
| Stop Loss | Mandatory | Never submit an order without a valid SL |
| Max deviation / slippage | Configurable (points) | Passed to MT5 `order_send` and checked against entry drift |
| Commission per lot | Configurable (account currency) | Used when MT5 `symbol_info` omits Raw/Zero commission |
| Post-rejection / post-loss cooldown | Configurable (e.g., `15–30 min`) | Prevents proposal spam on repeated scans |
| Rollover & news blackout | Enabled | Manual blackout windows in v1 + automatic daily rollover window |
| Martingale / grid | Disabled | Permanently disabled in v1 |
| Maximum spread | Configurable | Based on observed broker conditions |
| Manual kill switch | Enabled | Immediately blocks all new orders and cancels queued approvals |

These are conservative development defaults, not personalized financial advice. Do not raise limits merely to make a trade pass.

### 7.2 Position sizing and broker cost modeling

Use MT5's live symbol/account calculation functions (`order_calc_profit` and `order_calc_margin`) in the account's native currency (`USD` or `USC`):

```text
risk_budget      = account_equity * risk_percent
estimated_costs  = spread_cost + (configured_commission_per_lot * volume) + slippage_buffer
estimated_loss   = abs(order_calc_profit(side, symbol, volume, entry, stop_loss)) + estimated_costs
```

- Account for **commission** via `TradingSettings.commissionPerLot` (since Exness Raw Spread / Zero commission is not reliably returned by `symbol_info`, and actual realized commission is reconciled later from MT5 deals).
- Account for **swap** awareness in reports and trade history (noting Wednesday triple-swap on XAUUSD).

Choose the largest broker-valid volume only if:

- It respects `volume_min`, `volume_max`, and `volume_step`.
- `estimated_loss <= risk_budget`.
- Required margin (`order_calc_margin`) is available with a safety buffer (accounting for potential Exness High Margin Requirements).
- Broker `order_check` succeeds.

If `volume_min` exceeds `risk_budget`, **reject the trade (`RISK_REJECTED`)**. Never widen risk limits, tighten SL artificially, remove SL, or force a minimum lot.

### 7.2.1 Daily and weekly drawdown calculation

Calculate daily and weekly loss percentage on an **equity basis** (capturing both realized P/L and floating drawdown):

```text
drawdown_percent = ((starting_equity - current_equity) / starting_equity) * 100
```

- Capture `starting_equity` at the configured broker day boundary (`DailyRiskSnapshot`) and week boundary (`WeeklyRiskSnapshot`).
- Detect balance operations (`DEAL_TYPE_BALANCE` deposits/withdrawals) from MT5 deal history and adjust baseline `starting_equity` accordingly (logged in `AuditLog`) so manual deposits or withdrawals are never miscounted as trading profit or loss.

### 7.3 \$100–\$500 account consideration (Standard vs Standard Cent)

On a **Standard Exness account**, XAUUSD contract size is `100` troy ounces and `volume_min = 0.01` lot (~1 troy ounce of exposure). A \$1 gold price move equals roughly \$1 P/L at `0.01` lot.

- At `0.25%` risk, the budget is only **\$0.25 on \$100** and **\$1.25 on \$500**.
- Because normal M15 XAUUSD Stop Loss distances are often \$3–\$10+ (plus spread), **nearly all valid setups on a Standard account with \$100–\$500 will rightly be rejected (`NO TRADE`)**.

**Recommended solution for \$100–\$500 capital — Exness Standard Cent (`XAUUSDc`):**

- On a Standard Cent account, account balances are denominated in cents (`USC`, so \$100 = `10,000 USC`, \$500 = `50,000 USC`) and cent-lot contract exposure is 1/100th of a standard lot.
- With `XAUUSDc`, a `0.25%–0.5%` risk budget (`25–250 USC`) can realistically accommodate normal M15 gold Stop Loss distances without breaching capital-preservation rules.
- Because the risk engine always reads live `trade_contract_size`, `volume_min`, `volume_step`, and `order_calc_profit` from MT5, the same codebase supports both Standard (`USD`) and Cent (`USC`) accounts seamlessly.

### 7.4 Mandatory pre-trade checks

Run before creating a proposal **and again** inside the execution lock right before `order_send`:

- Account login, server, and `trade_mode` match configuration (`DEMO` unless live dual-lock is active).
- Terminal connected, `terminal_info().trade_allowed == True`, and `account_info().trade_allowed == True`.
- Kill switch is **not** active (checked via Redis flag) and system is not paused.
- Exact symbol exists, is visible, and has a valid `filling_mode`.
- Quote timestamp is fresh (within configured max staleness).
- Bid/ask and spread are valid and within `maxSpread`.
- Not inside daily rollover break or an active `BlackoutWindow`.
- Proposal is not expired, not cancelled, and has not already been used.
- User approval is valid, belongs to this proposal, and idempotency key has not been executed.
- Current market price has not drifted beyond `maxDeviationPoints` from proposal reference.
- SL/TP direction and broker `trade_stops_level` / `trade_freeze_level` are valid.
- Lot size is aligned to `volume_step`, and `estimated_loss <= risk_budget`.
- Daily loss limit, weekly loss limit (including manual-reset lock), max open positions, and margin buffer checks pass.
- MT5 `order_check` succeeds.

If any check fails, reject execution, record the exact failure reason, and publish an SSE update.

---

## 8. Semi-Automatic Approval, Manual Close & Kill Switch Workflows

### 8.1 Proposal states

`DRAFT → PENDING_APPROVAL → APPROVED → EXECUTING → EXECUTED`

Alternative terminal states: `REJECTED`, `EXPIRED`, `CANCELLED`, `RISK_REJECTED`, `AI_VETOED`, `EXECUTION_FAILED`.

### 8.2 Approval screen must show

- Account badge (**DEMO** vs **LIVE**) and account type (**STANDARD** vs **CENT**)
- Symbol and direction (`BUY` / `SELL`)
- Strategy name/version and timeframe
- Live bid/ask and spread (streamed via SSE)
- Proposed entry reference, Stop Loss, Take Profit
- Calculated volume (lots)
- Estimated maximum loss in native currency (`USD` or `USC`), USD equivalent (if Cent), and `%` of equity
- Estimated reward/risk ratio and required margin estimate
- AI explanation, risk warnings, invalidation notes (or clear `AI Unavailable` fallback notice)
- Proposal creation time and live expiry countdown
- **Approve** and **Reject** buttons

### 8.3 Approval safety

- Approval must be an authenticated `POST` request protected against CSRF.
- Approval is single-use and bound to a unique `idempotencyKey`.
- Proposal expires after a short configurable period (default `60–180` seconds, matched to M15 setup cadence and browser/SSE alert delivery).
- Execution re-fetches live price and re-runs all risk checks; if deviation, spread, or estimated loss exceeds limits, invalidate approval (`EXECUTION_FAILED` / `EXPIRED`) and do not execute.
- A double-click or retried request must never create duplicate orders.

### 8.4 Manual position close workflow

The owner can manually close an open position from the dashboard without opening the MT5 desktop terminal:

1. User clicks **Close Position** on an open position and confirms in a modal (showing current floating P/L, bid/ask, and spread).
2. Frontend sends `POST /api/v1/positions/:id/close` with a unique `idempotencyKey` (CSRF-protected).
3. API verifies the position is `OPEN` and no close is already in flight, marks `closeRequestedAt`, writes an `AuditLog` entry, and enqueues a `CLOSE_POSITION` command to Redis Streams.
4. Worker acquires the per-account execution lock, verifies the position ticket still exists on MT5, checks terminal/account connectivity, resolves `filling_mode`, and submits an opposite `TRADE_ACTION_DEAL` order referencing `position = brokerPositionId` with configured `deviation` and `magic`.
5. Worker reconciles the closing deal from MT5 history (actual close price, profit, commission, swap), updates the API (`Position.status = CLOSED`, `TradeOrder` with `actionType = CLOSE`, `TradeEvent`), releases the lock, and pushes an SSE update.
6. **Note:** Manual close is a **risk-reducing action** — it remains permitted even when the system is paused, loss limits are tripped, or the Kill Switch is active.

### 8.5 Kill switch behavior

Activating the Kill Switch (`POST /api/v1/system/kill-switch`) must:

1. Immediately set a durable DB state **and** a fast Redis key (`system:kill_switch = 1`).
2. Cancel all `PENDING_APPROVAL` proposals and abort any queued `APPROVED` jobs before `order_send` (checked inside the execution lock).
3. Block all new strategy proposals and order openings until explicitly resumed (`POST /api/v1/system/resume` after health/risk verification).
4. **Preserve existing broker-side SL/TP** on any open positions (does not silently force-close at bad spreads; the user can explicitly use Manual Close if desired).
5. Record an immutable `AuditLog` entry and broadcast an immediate SSE alert.

---

## 9. Database Architecture (Prisma Model Plan)

Use PostgreSQL with Prisma (owned exclusively by the Node.js API). Store money, prices, volumes, and percentages using `Decimal`, never JavaScript floats. Store all timestamps in UTC.

### Core models

| Model | Purpose | Important fields |
| --- | --- | --- |
| `User` | Dashboard owner | `id`, `email`, auth relation, `role` |
| `BrokerAccount` | MT5 account metadata (no plaintext password) | `broker`, `platform`, `login`, `server`, `accountCurrency`, `currencyScale`, `accountType` (`STANDARD`/`CENT`), `marginMode`, `magicNumber`, `mode` (`DEMO`/`LIVE`), `liveUnlockedAt`, `liveUnlockedBy`, `isEnabled` |
| `TradingSettings` | Risk, cost, and strategy settings | `symbol`, `riskPercent`, `dailyLossLimit`, `weeklyLossLimit`, `maxPositions`, `maxPendingProposals`, `maxSpread`, `maxDeviationPoints`, `commissionPerLot`, `approvalExpirySeconds`, `postRejectionCooldownMinutes`, `rolloverBlackoutMinutes`, `sessionWindows` JSON, `aiFallbackMode`, `isPaused`, `killSwitchActive` |
| `Strategy` | Strategy configuration/version | `name`, `version`, `timeframe`, `parameters` JSON, `isActive` |
| `BlackoutWindow` | Scheduled / manual news blackouts | `title`, `reason`, `startsAt`, `endsAt`, `isActive`, `createdBy` |
| `MarketSnapshot` | Candle/indicator snapshot | `symbol`, `timeframe`, `candleTime`, `bid`, `ask`, `spread`, `indicators` JSON |
| `TradeProposal` | Deterministic setup + AI review | `dedupKey` (unique), `strategyId`, `symbol`, `direction`, `entry`, `stopLoss`, `takeProfit`, `volume`, `riskAmount`, `rewardRisk`, `rationale`, `aiStatus`, `aiModel`, `aiPromptVersion`, `aiLatencyMs`, `aiTokensUsed`, `aiRawResponse` JSON, `status`, `expiresAt` |
| `TradeApproval` | User's approval/rejection | `proposalId`, `userId`, `decision`, `approvedAt`, `idempotencyKey` (unique) |
| `TradeOrder` | Broker order result (open or close) | `proposalId` (nullable for manual close), `positionId`, `actionType` (`OPEN`/`CLOSE`), `brokerOrderId`, `brokerDealId`, `symbol`, `side`, `volume`, `entryPrice`, `stopLoss`, `takeProfit`, `fillingMode`, `deviationPoints`, `retcode`, `brokerComment`, `status` |
| `Position` | Open/closed MT5 position | `brokerPositionId` (unique), `symbol`, `side`, `volume`, `openPrice`, `currentPrice`, `closePrice`, `stopLoss`, `takeProfit`, `profit`, `commission`, `swap`, `magicNumber`, `isBotManaged`, `status`, `closeRequestedAt`, `openedAt`, `closedAt` |
| `TradeEvent` | Order/position lifecycle log | `tradeOrderId`, `positionId`, `eventType`, `payload` JSON, `createdAt` |
| `DailyRiskSnapshot` | Daily P/L and limit tracking | `date`, `startingEquity`, `realizedPnl`, `unrealizedPnl`, `lossLimit`, `isTripped` |
| `WeeklyRiskSnapshot` | Weekly P/L and manual reset gate | `weekStart`, `startingEquity`, `realizedPnl`, `unrealizedPnl`, `lossLimit`, `isTripped`, `resetByUserId`, `resetAt`, `resetNote` |
| `AuditLog` | Security and user actions | `actorId`, `action`, `entityType`, `entityId`, `metadata` JSON, `ip`, `createdAt` |
| `SystemEvent` | Worker health and integration events | `service`, `severity`, `message`, `metadata` JSON, `createdAt` |

### Data rules

- Add unique constraints for broker account identity `(login, server)`, `TradeProposal.dedupKey`, `TradeApproval.idempotencyKey`, `Position.brokerPositionId`, and broker order/deal IDs where supported.
- A proposal can have at most one successful `OPEN` execution.
- Use indexes on `status`, `symbol`, `createdAt`, `expiresAt`, `candleTime`, and broker ticket IDs.
- Preserve immutable `AuditLog` and `TradeEvent` records; never silently overwrite execution history.
- Never store MT5 trading passwords, API secrets, or terminal credentials in ordinary database columns.
- Keep raw `MarketSnapshot` retention configurable (e.g., 30–90 days) to control database size.

### Suggested enums

```prisma
enum AccountMode {
  DEMO
  LIVE
}

enum BrokerAccountType {
  STANDARD
  CENT
}

enum TradeDirection {
  BUY
  SELL
}

enum ProposalStatus {
  DRAFT
  PENDING_APPROVAL
  APPROVED
  REJECTED
  EXPIRED
  RISK_REJECTED
  AI_VETOED
  EXECUTING
  EXECUTED
  EXECUTION_FAILED
  CANCELLED
}

enum AiAnalysisStatus {
  COMPLETED
  VETOED
  UNAVAILABLE
  SKIPPED
}

enum ApprovalDecision {
  APPROVE
  REJECT
}

enum OrderActionType {
  OPEN
  CLOSE
}

enum OrderStatus {
  REQUESTED
  ACCEPTED
  PARTIALLY_FILLED
  FILLED
  REJECTED
  CANCELLED
  UNKNOWN
}

enum PositionStatus {
  OPEN
  CLOSING
  CLOSED
  UNKNOWN
}
```

---

## 10. API Design

All user endpoints require authentication and CSRF protection (for state-changing requests) except `/health`. Validate all payloads with Zod.

### 10.1 Auth, health, and real-time SSE stream

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `POST` | `/api/v1/auth/login` | Authenticate user and set HTTP-only session cookie |
| `POST` | `/api/v1/auth/logout` | Invalidate active session |
| `GET` | `/api/v1/auth/me` | Return current authenticated user & session metadata |
| `GET` | `/health` | Unauthenticated liveness probe (no sensitive data) |
| `GET` | `/api/v1/system/health` | Authenticated MT5, worker heartbeat, Redis, DB, and queue status |
| `GET` | `/api/v1/stream` | Authenticated **Server-Sent Events (SSE)** stream |

**SSE event types pushed on `/api/v1/stream`:**

- `quote.updated` — throttled live bid/ask/spread & UTC timestamp (e.g., max 1–2 Hz)
- `proposal.created` / `proposal.updated` / `proposal.expired`
- `order.updated` — execution check, submission, fill, or rejection updates
- `position.updated` / `position.closed` — live floating P/L and closure sync
- `system.health` — MT5 connection, worker heartbeat, kill switch, pause state
- `risk.alert` — daily/weekly loss limit warnings, high spread, stale data

**SSE transport & reverse-proxy requirements:**

- Prefer a **same-origin reverse proxy** (Nginx in production, Next.js rewrites in dev) forwarding `/api/*` to Express so HTTP-only cookies, CSRF, and `EventSource` work without cross-origin credential complexity (if cross-origin, configure strict `CORS_ORIGIN` and `EventSource(..., { withCredentials: true })`).
- Set response headers `Content-Type: text/event-stream`, `Cache-Control: no-cache, no-transform`, `Connection: keep-alive`, and `X-Accel-Buffering: no` (and `proxy_buffering off` in Nginx) so proxies do not buffer live events.
- Include heartbeat comments (`: ping\n\n` every 15–30s) and `Last-Event-ID` support so clients recover cleanly after network drops (re-fetching snapshots via TanStack Query on reconnect).

### 10.2 Dashboard, account, positions, and orders

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `GET` | `/api/v1/dashboard/summary` | Balance, equity, native currency + USD equivalent, daily/weekly P/L, status |
| `GET` | `/api/v1/account` | Connected account metadata (Standard/Cent, Demo/Live, symbol specs) |
| `GET` | `/api/v1/market/xauusd` | Latest quote, spread, and indicator summary |
| `GET` | `/api/v1/market/xauusd/candles` | Historical UTC candles for charts (`?timeframe=M15&limit=200`) |
| `GET` | `/api/v1/positions` | Current open and recent closed positions |
| `POST` | `/api/v1/positions/:id/close` | Request audited manual close of an open position (requires `idempotencyKey`) |
| `GET` | `/api/v1/orders` | Trade history (orders, deals, retcodes, fees, commissions, swaps) |

### 10.3 Proposals and approvals

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `GET` | `/api/v1/proposals` | List proposals (filterable by status/date) |
| `GET` | `/api/v1/proposals/:id` | Proposal detail including AI rationale and risk breakdown |
| `POST` | `/api/v1/proposals/:id/approve` | Approve one proposal (requires `idempotencyKey`) |
| `POST` | `/api/v1/proposals/:id/reject` | Reject one proposal (triggers optional cooldown) |
| `POST` | `/api/v1/proposals/:id/cancel` | Cancel pending proposal |

### 10.4 Settings, strategies, blackouts, and risk controls

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `GET` | `/api/v1/settings/trading` | Read trading and risk settings |
| `PATCH` | `/api/v1/settings/trading` | Update allowed settings (audited) |
| `GET` | `/api/v1/strategies` | List strategies and active parameters |
| `PATCH` | `/api/v1/strategies/:id` | Enable/disable or update safe strategy parameters |
| `GET` | `/api/v1/blackouts` | List scheduled/manual news blackout windows |
| `POST` | `/api/v1/blackouts` | Create a blackout window |
| `DELETE` | `/api/v1/blackouts/:id` | Remove a blackout window |
| `POST` | `/api/v1/risk/weekly/reset` | Manual review & reset of a tripped weekly loss limit (requires note) |
| `POST` | `/api/v1/system/pause` | Pause new proposals and openings |
| `POST` | `/api/v1/system/resume` | Resume after health and risk checks pass |
| `POST` | `/api/v1/system/kill-switch` | Emergency block of all new orders and cancel queued approvals |
| `GET` | `/api/v1/audit-logs` | View immutable audit events |
| `GET` | `/api/v1/reports/performance` | Performance metrics (win rate, net P/L, profit factor, average R, drawdown) |

### 10.5 Internal worker endpoints (`/api/internal/v1/*`)

Protected by `Authorization: Bearer <WORKER_AUTH_TOKEN>` and restricted to private network / firewall allowlist:

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `GET` | `/api/internal/v1/runtime-state` | Fetch active `TradingSettings`, `Strategy`, `BlackoutWindow`s, `Daily/WeeklyRiskSnapshot`, pause/kill-switch state |
| `GET` | `/api/internal/v1/reconciliation/stuck` | Fetch proposals stuck in `EXECUTING` or positions stuck in `CLOSING` beyond timeout threshold |
| `POST` | `/api/internal/v1/proposals/:id/claim` | Atomic transition `APPROVED → EXECUTING` (`WHERE status='APPROVED' AND expiresAt > NOW()`) preventing expiry/cancel races |
| `POST` | `/api/internal/v1/heartbeat` | Report worker liveness, MT5 connection, terminal/account status |
| `POST` | `/api/internal/v1/account/sync` | Sync account balance, equity, currency scale, margin mode, symbol specs |
| `POST` | `/api/internal/v1/market/snapshot` | Persist closed candle + indicator snapshot |
| `POST` | `/api/internal/v1/proposals` | Persist newly generated deterministic + AI proposal |
| `POST` | `/api/internal/v1/proposals/expire` | Mark expired pending proposals |
| `POST` | `/api/internal/v1/orders/result` | Persist `order_check`/`order_send` outcome, retcode, and reconciliation |
| `POST` | `/api/internal/v1/positions/sync` | Sync open/closed positions, deals, commission, swap, and P/L |
| `POST` | `/api/internal/v1/events` | Record worker `SystemEvent` / `TradeEvent` |

Do not expose any public endpoint that lets the browser submit arbitrary new MT5 order parameters. The only paths to MT5 execution are (1) a valid approved proposal passing all risk checks, or (2) an audited manual close of an existing open position.

---

## 11. Python Worker Design

### Folder structure (`apps/worker/`)

```text
apps/worker/
├── app/
│   ├── main.py
│   ├── config.py
│   ├── api_client.py          # Authenticated HTTP client for /api/internal/v1/*
│   ├── queue/
│   │   ├── streams.py         # Redis Streams consumer group (EXECUTE_PROPOSAL, CLOSE_POSITION)
│   │   ├── pubsub.py          # Redis Pub/Sub publisher for live SSE events
│   │   └── locks.py           # Distributed per-account execution lock + kill-switch check
│   ├── mt5/
│   │   ├── client.py          # Initialize, reconnect, AlgoTrading/account checks
│   │   ├── account.py         # Account info, Cent/Standard detection, margin mode
│   │   ├── symbols.py         # Symbol discovery, filling_mode resolution, specs
│   │   ├── market_data.py     # Ticks, candles, server-time-to-UTC normalization
│   │   └── execution.py       # order_check, order_send (open & close), reconciliation
│   ├── market/
│   │   ├── candles.py
│   │   ├── indicators.py
│   │   └── features.py
│   ├── strategy/
│   │   ├── base.py
│   │   ├── xauusd_v1.py
│   │   └── signal.py
│   ├── ai/
│   │   ├── client.py          # OpenAI structured output + timeout/fallback
│   │   ├── prompts.py
│   │   └── schemas.py
│   ├── risk/
│   │   ├── sizing.py          # Standard & Cent sizing, order_calc_profit, costs
│   │   ├── limits.py          # Daily/weekly loss, blackout, rollover, cooldown
│   │   └── validator.py
│   ├── monitoring/
│   │   ├── health.py
│   │   └── logging.py
│   └── jobs/
│       ├── market_scan.py
│       ├── command_consumer.py
│       ├── proposal_expiry.py
│       └── position_sync.py
├── backtest/
│   ├── data_loader.py         # MT5 historical candle & tick exporter
│   ├── engine.py              # Event-driven replay with spread, commission, swap, slippage
│   └── metrics.py             # Drawdown, profit factor, expectancy, walk-forward report
├── tests/
├── requirements.txt
├── .env.example
└── README.md
```

### Queue, MT5 thread-safety, and locking protocol

- **MT5 single-lock serialization (`MT5Client`):** The official `MetaTrader5` Python package wraps a single-process, non-thread-safe IPC bridge to the local terminal. All `mt5.*` calls across polling, sync, and execution jobs must go through one centralized `MT5Client` protected by an in-process `threading.RLock()` (or dedicated single-threaded MT5 actor queue) so concurrent threads never invoke the C-extension simultaneously.
- **Command stream (`commands:trading`):** Node.js API appends validated JSON messages (`type: EXECUTE_PROPOSAL | CLOSE_POSITION`, `commandId`, `idempotencyKey`, `payload`, `createdAt`).
- **Consumer group:** Python worker consumes with `XREADGROUP`, processes sequentially per account, and calls `XACK` only after reconciliation and internal API persistence succeed. Unprocessable messages move to a dead-letter stream (`commands:trading:dlq`) and trigger an alert.
- **Distributed execution lock:** Before any `order_send` (open or close), acquire `lock:execution:<accountLogin>` in Redis with a short TTL (e.g., `30s`) and unique lock token.
- **Live event Pub/Sub (`events:sse`):** Worker publishes throttled quotes and state updates to Redis Pub/Sub; Node.js API fans them out to connected authenticated SSE clients.

### Worker job schedule (initial suggestion)

- Quote polling & SSE publish: every `1–2` seconds (throttled on unchanged tick).
- Runtime state refresh (`GET /api/internal/v1/runtime-state`): every `10–30` seconds and immediately on `settings.updated` Pub/Sub event.
- Candle refresh: on each new M5/M15/H1 candle close.
- Strategy evaluation: only on completed M15 candle (and optional M5 confirmation).
- Command consumer (`EXECUTE_PROPOSAL` / `CLOSE_POSITION`): continuous blocking `XREADGROUP`.
- Proposal expiry check: every `5–10` seconds.
- Position/account synchronization: every `2–5` seconds.
- Stuck-state reconciliation sweep: on worker startup and every `30–60` seconds.
- Heartbeat: every `10` seconds.
- Daily/weekly risk snapshot: at configured broker/server day/week boundary.

### Open-order execution sequence

1. Consume `EXECUTE_PROPOSAL` job from Redis Stream.
2. Acquire per-account Redis execution lock.
3. Check Redis kill-switch flag and latest `runtime-state` (pause/risk/blackouts); abort if blocked.
4. Atomically claim the proposal via `POST /api/internal/v1/proposals/:id/claim` (`APPROVED → EXECUTING`, checking expiry and `idempotencyKey`); abort if `409 Conflict`.
5. Verify MT5 terminal connection, `trade_allowed`, and account identity/mode.
6. Refresh live symbol info, tick, account equity, open positions, and active blackouts.
7. Revalidate price deviation, spread, stop distances, lot sizing, and risk/loss limits.
8. Build `TRADE_ACTION_DEAL` request with resolved `filling_mode`, `deviation`, `magic`, `comment`, mandatory `sl`, and `tp`.
9. Call `order_check`.
10. If valid, call `order_send` once.
11. Reconcile resulting order/deal/position from MT5 (matching ticket and `magic`/`comment`).
12. Persist full request/result/retcode via internal API, publish SSE event, `XACK` message, and release lock.

### Manual-close execution sequence

1. Consume `CLOSE_POSITION` job from Redis Stream.
2. Acquire per-account Redis execution lock.
3. Verify MT5 connection, account identity, and that `brokerPositionId` is still open.
4. Fetch fresh tick and resolve `filling_mode`.
5. Build opposite `TRADE_ACTION_DEAL` request referencing `position = brokerPositionId`, `volume = position.volume`, `deviation`, `magic`, and close `comment`.
6. Call `order_check`, then `order_send` once.
7. Reconcile closing deal (close price, realized profit, commission, swap) from MT5 history.
8. Persist result via internal API, publish SSE `position.closed` event, `XACK`, and release lock.

### Stuck `EXECUTING` / `CLOSING` state recovery

If the worker crashes or times out after claiming a proposal (`EXECUTING`) or starting a manual close (`CLOSING`), **do not blindly retry**:

1. On startup (before consuming new commands) and on a periodic `30–60s` sweep, query `GET /api/internal/v1/reconciliation/stuck` for any record stuck beyond `45` seconds.
2. Inspect MT5 `positions_get()`, `orders_get()`, and `history_deals_get()` matching `magic` + `comment` (`AGY:<proposalShortId>`) or `brokerPositionId`.
3. If the order/deal exists on MT5, persist the reconciled final state (`EXECUTED` or `CLOSED`) and publish an SSE update.
4. If MT5 is connected and verified to have no matching order/deal, mark the proposal `EXECUTION_FAILED` (or revert `CLOSING → OPEN` and alert the user).
5. If MT5 is disconnected or the outcome remains ambiguous, set status to `UNKNOWN`, alert the user, and **fail closed** (block all new order openings) until reconciliation succeeds.

---

## 12. Frontend Pages

All authenticated pages display a persistent status header showing: **DEMO / LIVE** badge, **STANDARD / CENT** badge, **SSE connection status**, MT5/worker health dot, and **Kill Switch** button.

1. **Login** — private user authentication.
2. **Dashboard** — account status, balance/equity (native currency + USD equivalent for Cent), daily/weekly P/L progress bars, active proposals, worker health, kill switch.
3. **Market Overview** — live XAUUSD chart (Lightweight Charts), SSE bid/ask & spread, selected timeframes, indicator values, session/rollover/blackout indicator.
4. **AI Proposals** — live-updating pending, approved, rejected, vetoed, and expired proposals (with optional browser notification/sound on new proposal).
5. **Proposal Detail** — rationale, risk breakdown (native + USD equivalent), SL/TP, volume, expiry countdown, Approve/Reject actions.
6. **Open Positions** — live SSE floating P/L, entry/SL/TP, bot-managed vs manual badge, and **Close Position** button with confirmation dialog.
7. **Trade History** — orders, deals, closed trades, retcodes, commissions, swaps, and realized outcomes.
8. **Risk & Strategy Settings** — risk limits, weekly loss reset control, max spread, deviation, commission per lot, cooldown, strategy toggles, and **News Blackout Windows** manager.
9. **Reports** — win rate, net P/L, profit factor, expectancy, drawdown, average R.
10. **System Health** — MT5 connection, Algo Trading flag, worker heartbeat, Redis stream lag, SSE client status, database status, last successful sync.
11. **Audit Logs** — approvals, rejections, manual closes, setting changes, weekly resets, pauses, kill switches, executions, and errors.

---

## 13. Security Requirements

- Keep MT5 credentials only on the private Windows worker host, preferably using Windows Credential Manager or another secure secret store.
- Never send broker password or terminal credentials to the browser or API database.
- Keep OpenAI API keys, session secrets, `WORKER_AUTH_TOKEN`, and database secrets in server-side environment variables / secret manager.
- Use TLS for dashboard/API, SSE, and worker-to-API communication.
- Restrict internal API endpoints (`/api/internal/v1/*`) and Redis access via firewall/private network allowlists + authentication.
- Authenticate `/api/v1/stream` (SSE) using the same HTTP-only session cookie and enforce per-user concurrent connection limits.
- Use strong authentication, CSRF protection on state-changing routes, rate limits on auth/approval/close endpoints, and optional MFA.
- Validate all API payloads, internal worker payloads, and Redis Stream messages with Zod/Pydantic.
- **Live Mode Dual-Lock:** Prevent accidental live trading by requiring **all three**: (1) `MT5_ACCOUNT_MODE=LIVE` on the worker, (2) MT5 `account_info().trade_mode == ACCOUNT_TRADE_MODE_REAL`, and (3) explicit live unlock recorded in `BrokerAccount` + `AuditLog`. Any mismatch fails closed.
- Redact secrets from logs and error traces; back up PostgreSQL and test restore procedures.
- On disconnection or uncertain execution result, fail closed: no new order until reconciliation succeeds.

---

## 14. Monitoring and Alerts

Alert the user immediately (via SSE dashboard toast + optional browser Web Notification / audio chime) when:

- MT5 terminal disconnects, Algo Trading is turned off, or account identity changes.
- Worker heartbeat is missed or Redis Stream lag grows.
- Market data is stale or spread exceeds `maxSpread`.
- A new proposal is ready or approaching expiry.
- An order (open or manual close) is filled, rejected, or has an uncertain execution result.
- Daily or weekly loss limit is reached.
- A position is opened or closed (by SL, TP, or manual close).
- Kill switch is activated or resumed.

For v1, rely on SSE dashboard + browser notifications; add Telegram/email notifications optionally after secure integration is configured. Never include broker credentials in alerts.

---

## 15. Testing and Validation Plan

### Unit tests

- Indicator calculations against known candle fixtures (including UTC timestamp normalization).
- Signal generation, deduplication key, cooldown, rollover, and blackout window filtering.
- Lot rounding, `volume_step`, and `filling_mode` bitmask resolution.
- Profit/loss and cost estimation for **both Standard (`USD`) and Cent (`USC`)** accounts against MT5 calculation results.
- Risk-limit edge cases (daily loss limit, weekly loss limit + manual reset, margin buffer).
- AI schema validation, veto handling, and `AI_UNAVAILABLE` fallback behavior.
- Proposal expiry, single-use approval, idempotency, and duplicate execution protection.

### Integration tests

- MT5 initialize/shutdown, reconnect, `terminal_info().trade_allowed`, and account/symbol verification.
- Historical candle retrieval and UTC conversion.
- `order_check`, demo `order_send` with minimum valid volume, `magic`, `comment`, and attached SL/TP.
- **Manual close flow** end-to-end (API → Redis Stream → worker close → MT5 deal reconciliation → SSE event).
- Kill switch canceling queued `APPROVED` jobs while still allowing manual close of an open position.
- SSE stream delivery, heartbeat, and client reconnect recovery.
- Uncertain-order timeout reconciliation (ensuring zero duplicate trades).

### Strategy validation

- Backtest across different market regimes using `apps/worker/backtest/`.
- Include spread, commission, swap, slippage, and rollover/execution assumptions.
- Use out-of-sample testing and walk-forward validation; avoid optimizing on the reporting window.
- Track maximum drawdown, expectancy, profit factor, average R, and consecutive losses.
- Compare demo forward-test results against backtest assumptions.

### Release gates

Do not enable live mode until:

1. The system has completed a documented demo test period (on the same account type intended for live, e.g., Standard Cent).
2. No duplicate-order, missing-SL, or reconciliation defects remain.
3. Reconnection, kill-switch, manual-close, and uncertain-order reconciliation drills pass.
4. Risk and cost calculations match MT5 live/demo results within defined tolerances.
5. User has reviewed the strategy and accepts the possibility of losing the full trading capital.

---

## 16. Development Roadmap

| Phase | Deliverables | Completion criteria |
| --- | --- | --- |
| 0. Project foundation | Monorepo (`apps/web`, `apps/api`, `apps/worker`, `packages/shared`), Docker dev stack, Prisma schema, linting, secrets policy | Services boot locally; `/health` and DB migrations pass |
| 1. MT5 connectivity | Windows setup, terminal verification, Standard/Cent (`XAUUSDc`) detection, `filling_mode`, UTC normalization | Read-only demo account & symbol specs sync to API |
| 2. Market data & SSE | XAUUSD ticks/candles, internal ingest API, Redis Pub/Sub → SSE `/api/v1/stream`, chart UI | Live quotes and candles render in real time on dashboard |
| 3. Indicators, strategy & backtest | EMA/RSI/ATR, H1/M15 rules, dedup/cooldown/rollover/blackout filters, `backtest/` engine | Reproducible signals from fixtures and baseline backtest report |
| 4. Risk engine | Standard & Cent sizing, cost modeling, daily/weekly limits + reset, pre-trade validator | Invalid/high-risk proposals always rejected (`RISK_REJECTED`) |
| 5. AI explanation | Trigger-only AI commentary/veto, strict Pydantic schema, timeout fallback, audit fields | AI cannot alter prices/lots or bypass deterministic rules |
| 6. Approval workflow | Proposal screen, SSE alerts, approve/reject, expiry, idempotency, kill switch | Only a valid single-use approval enters Redis Stream |
| 7. Demo execution & manual close | Redis Stream consumer, execution lock, `order_check`/`order_send`, manual close endpoint, reconciliation | Demo open & manual close work with SL/TP and zero duplicates |
| 8. Dashboard & reports | Positions, trade history, blackout manager, weekly reset, system health, audit logs, reports | Full lifecycle visible and controllable from dashboard |
| 9. Demo forward testing | Logs, metrics, failure drills (disconnect, timeout, kill switch, AI outage) | Release checklist signed off |
| 10. Limited live pilot | Dual-lock live unlock, smallest risk-compliant size (Cent recommended for \$100–\$500) | Only if all gates pass; otherwise remain on demo |

### Suggested build order

Build and verify one phase before starting the next. Do not begin with AI prompts or live order placement. First make the MT5 connector and Standard/Cent verification reliable, then market data + SSE, strategy + backtest, deterministic risk checks, approval, and finally demo open/close execution.

---

## 17. Environment Configuration

Example `.env.example` (placeholders only):

```env
NODE_ENV=development
API_PORT=5000
CORS_ORIGIN=http://localhost:3000
NEXT_PUBLIC_API_URL=/api
DATABASE_URL=postgresql://USER:PASSWORD@HOST:5432/trading_agent
REDIS_URL=redis://:PASSWORD@HOST:6379
REDIS_STREAM_COMMANDS=commands:trading
REDIS_PUBSUB_CHANNEL=events:sse

APP_SESSION_SECRET=replace_with_long_random_secret
WORKER_API_BASE_URL=http://127.0.0.1:5000
WORKER_AUTH_TOKEN=replace_with_long_random_token

MT5_TERMINAL_PATH=C:\Program Files\MetaTrader 5\terminal64.exe
MT5_ACCOUNT_LOGIN=DEMO_ACCOUNT_NUMBER
MT5_ACCOUNT_SERVER=Exness-MT5Demo
MT5_ACCOUNT_MODE=DEMO
MT5_ACCOUNT_TYPE=CENT
MT5_SYMBOL=XAUUSDc
MT5_MAGIC_NUMBER=260101

OPENAI_API_KEY=server_side_secret
OPENAI_MODEL=configure_supported_model_name
AI_TIMEOUT_SECONDS=5
AI_FALLBACK_MODE=DETERMINISTIC_ONLY
```

Do not commit `.env`, credentials, account numbers, API keys, or terminal data directories to Git. Prefer Windows Credential Manager or a secure secret store over putting the MT5 password in `.env`.

---

## 18. Definition of Done for Version 1

Version 1 is complete when the owner can:

- Sign in to a private dashboard with live SSE updates.
- See the verified Exness MT5 demo account (Standard or Cent), currency scale, and exact XAUUSD symbol.
- View fresh UTC-normalized market data, spread, and indicator values in real time.
- Receive a structured, explainable trade proposal (with graceful fallback if AI is unavailable).
- See calculated volume and maximum estimated loss (including costs) in native currency and USD equivalent.
- Approve or reject the proposal before expiry.
- Have the system revalidate all conditions (including kill switch and price deviation) inside an execution lock before execution.
- See a demo market order submitted with valid `filling_mode`, `magic`, `comment`, Stop Loss, and Take Profit.
- Monitor the resulting open position live via SSE and **manually close** it from the dashboard with full deal reconciliation.
- Manage blackout windows, reset tripped weekly loss limits after review, pause trading, or activate the kill switch at any time.
- Review complete audit logs, trade history, system health, and performance reports.
- Verify that stale data, invalid approvals, excessive risk, AI failures, disconnects, and duplicate requests never cause an unintended order to be sent.

**Default production posture:** DEMO mode, semi-automatic approval, one XAUUSD position maximum, `MARKET` orders only, mandatory Stop Loss, audited manual close, and fail-closed execution.

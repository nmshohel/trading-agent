# Pre-Live Security & Version 1 Definition of Done Audit

**Architecture Reference:** `architecture.md` v1.2 (Section 13 & Section 18)  
**Default Repository Posture:** `DEMO` mode, `CENT` (`XAUUSDc`), semi-automatic approval, max 1 open XAUUSD position, `MARKET` orders only, mandatory Stop Loss, audited manual close, fail-closed execution.

---

## 1. Security Boundary Audit

| Control | Implementation | Status |
| --- | --- | --- |
| Single DB Owner | Only `apps/api` (Prisma) accesses PostgreSQL; `apps/worker` uses `/api/internal/v1/*` + Redis | Verified |
| Authentication | HTTP-only cookie sessions (`apps/api/src/routes/auth.ts`, `middleware/auth.ts`) with SHA-256 hashed tokens | Verified |
| Role Enforcement | `requireOwner` enforced on all state-changing trading/risk/settings/account endpoints | Verified |
| CSRF Protection | `requireCsrf` (`x-csrf-token` timing-safe compare) on all authenticated POST/PATCH/DELETE routes | Verified |
| Rate Limiting | `express-rate-limit` on `/auth/login`, `/proposals/:id/*`, `/positions/:id/close`, `/account/unlock-live` + Nginx `limit_req` | Verified |
| Internal Worker Auth | Timing-safe Bearer `WORKER_AUTH_TOKEN` + Nginx `/api/internal/` IP allowlist (`deny all` public) | Verified |
| Secret Redaction | Pino `redact` in `apps/api/src/lib/logger.ts` and `redact_mapping()` in `apps/worker/app/monitoring/logging.py` | Verified |
| No Arbitrary Browser Orders | Zero public endpoints accept arbitrary MT5 new-order params; entries require approved proposal + risk validation | Verified |
| Live Mode 3-Way Dual-Lock | Requires `MT5_ACCOUNT_MODE=LIVE` + MT5 `ACCOUNT_TRADE_MODE_REAL` + audited DB `liveUnlockedAt`/`liveUnlockedBy` | Verified |
| MT5 Thread-Safety | Centralized `MT5Client` (`apps/worker/app/mt5/client.py`) guarded by `threading.RLock()` | Verified |
| Default `.env.example` Posture | Strictly `MT5_ACCOUNT_MODE=DEMO` with placeholder secrets only | Verified |

---

## 2. Section 18 — Version 1 Definition of Done Matrix

- [x] Sign in to a private dashboard with live SSE updates (`/login`, `/api/v1/stream`, `AppShell`).
- [x] See the verified Exness MT5 demo account (Standard or Cent), currency scale, and exact XAUUSD symbol (`/api/v1/account`, `apps/worker/app/mt5/account.py`, `symbols.py`).
- [x] View fresh UTC-normalized market data, spread, and indicator values in real time (`/market`, Lightweight Charts, `market_data.py`, `indicators.py`).
- [x] Receive a structured, explainable trade proposal with graceful fallback if AI is unavailable (`apps/worker/app/ai/client.py`, `/proposals`, `/proposals/[id]`).
- [x] See calculated volume and maximum estimated loss (including costs) in native currency and USD equivalent (`sizing.py`, Proposal Detail UI).
- [x] Approve or reject the proposal before expiry (single-use `idempotencyKey`, countdown timer, post-rejection cooldown).
- [x] Revalidate all conditions (kill switch, Algo Trading, price deviation, spread, risk) inside a Redis execution lock before `order_send` (`execution.py`, `locks.py`).
- [x] Submit a demo market order with valid `filling_mode`, `magic`, `comment`, Stop Loss, and Take Profit (`execution.py`).
- [x] Monitor the resulting open position live via SSE and manually close it from the dashboard with full deal reconciliation (`/positions`, `POST /api/v1/positions/:id/close`, `execute_manual_close`).
- [x] Manage blackout windows, reset tripped weekly loss limits after review, pause trading, or activate the kill switch at any time (`/settings`, `AppShell`, `/api/v1/system/*`).
- [x] Review complete audit logs, trade history, system health, and performance reports (`/audit`, `/history`, `/health`, `/reports`).
- [x] Verify that stale data, invalid approvals, excessive risk, AI failures, disconnects, and duplicate requests never cause an unintended order to be sent (`test_failure_drills.py`, `test_phase10_live_dual_lock.py`, API Vitest suite).

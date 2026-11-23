# Demo Forward-Test Sign-Off Checklist (Phase 9 Release Gates)

Complete and sign off EVERY item below on an Exness MT5 **DEMO** account matching your intended live account type (Standard Cent `XAUUSDc` recommended for USD 100–500) before considering Phase 10 live unlock.

## 1. Documented Demo Forward-Test Period
- [ ] Connected to Exness MT5 Demo account (`MT5_ACCOUNT_MODE=DEMO`).
- [ ] Account type (`CENT` / `STANDARD`), currency scale (`USC=100` / `USD=1`), `filling_mode`, and UTC server offset verified in logs & `/health`.
- [ ] Minimum forward-test window completed (e.g., 2–4 weeks / sufficient trade sample across London & NY sessions).
- [ ] Live demo performance compared against `apps/worker/backtest/` walk-forward assumptions (spread, commission, swap, slippage).

## 2. Zero Execution Safety Defects
- [ ] Zero duplicate orders observed under double-click approvals or retries.
- [ ] Every executed order had a valid broker-attached Stop Loss and Take Profit.
- [ ] Every open order carried the configured `MT5_MAGIC_NUMBER` and `AGY:<shortId>` comment.
- [ ] Manual position close (`/api/v1/positions/:id/close`) tested end-to-end with reconciled close price, commission, swap, and profit.

## 3. Automated & Live Failure Drills Passed
- [ ] **Drill 1 (MT5 Disconnect):** Disconnecting MT5 terminal triggers fail-closed error, stale/offline health alert, and clean reconnect.
- [ ] **Drill 2 (Algo Trading Off):** Toggling MT5 Algo Trading button off blocks execution cleanly (`ALGO_TRADING_DISABLED`).
- [ ] **Drill 3 (`order_send` Timeout Reconciliation):** Simulated timeout reconciles existing MT5 position/deal by `magic` + `comment` with zero duplicate orders.
- [ ] **Drill 4 (Kill Switch Mid-Queue):** Activating Kill Switch aborts queued `APPROVED` open jobs while preserving broker SL/TP and permitting manual close.
- [ ] **Drill 5 (AI Outage):** OpenAI timeout / 500 error cleanly falls back to `DETERMINISTIC_ONLY` with `AI Unavailable` UI warning.
- [ ] **Drill 6 (Idempotency & Lock Contention):** Replaying approval/close `idempotencyKey` or concurrent lock contention produces zero duplicate orders.
- [ ] **Drill 7 (SSE Drop & Reconnect):** Dropping API connection triggers SSE reconnect, `Last-Event-ID` replay, and TanStack Query refresh.

## 4. Risk & Infrastructure Readiness
- [ ] Risk engine `estimated_total_loss` matches actual MT5 deal P/L + spread/commission within tolerance.
- [ ] Daily loss limit (`1.0%`) and weekly loss limit (`2.0%` + manual review/reset) verified.
- [ ] Windows process supervision (`infra/windows/install-worker-service.ps1`) verified across reboot.
- [ ] PostgreSQL backup & restore (`infra/scripts/backup-postgres.ps1` & `restore-postgres.ps1`) verified on test DB.

## 5. Owner Risk Acknowledgement
- [ ] Owner reviewed strategy performance, drawdown, and failure behavior.
- [ ] Owner explicitly accepts that gold (XAUUSD) trading involves risk of losing 100% of allocated trading capital and that this software provides no profit guarantee.

**Signed off by:** ______________________ **Date (UTC):** ______________________

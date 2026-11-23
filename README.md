# Personal AI Forex Trading Agent

Semi-automatic XAUUSD decision-support and execution platform for Exness MT5 (DEMO-first, Standard & Standard Cent `XAUUSDc` support, SSE real-time updates, audited manual close).

See [architecture.md](./architecture.md) for the full v1.2 specification and [prompt.md](./prompt.md) for the phase-by-phase build roadmap.

## Monorepo Layout

- `apps/web` — Next.js (App Router) dashboard with same-origin `/api/*` proxy
- `apps/api` — Node.js / Express / Prisma API (sole PostgreSQL owner) + SSE server
- `apps/worker` — Python MT5 trading worker & backtesting module (Windows)
- `packages/shared` — Shared enums, Redis command schemas, SSE event types, constants
- `infra/` — Docker Compose (PostgreSQL 16 + Redis 7) and deployment scripts

## Quick Start (Phase 0)

1. Copy `.env.example` to `.env` and set local secrets.
2. Start PostgreSQL and Redis:
   ```powershell
   pnpm infra:up
   ```
3. Install dependencies, generate Prisma client, migrate, and seed:
   ```powershell
   pnpm install
   pnpm db:generate
   pnpm db:migrate
   pnpm db:seed
   ```
4. Run API and Web in separate terminals:
   ```powershell
   pnpm dev:api
   pnpm dev:web
   ```
5. Run checks and tests:
   ```powershell
   pnpm typecheck
   pnpm test
   ```

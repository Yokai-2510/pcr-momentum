# Frontend — Master Plan (Phase 10)

This is the index document for everything UI. Read it first, then drill into
the numbered siblings in this folder.

> **Prerequisite reading**: `docs/Frontend_Integration.md`, `docs/Frontend_Basics.md`,
> `docs/API.md`. The first defines the working environment; the second is the
> WebSocket / view contract; the third is the REST contract.

---

## 0. Goal

Ship a single-operator dashboard that the trader can run all day to:

1. Watch live positions, PnL, ΔPCR, and system health.
2. Adjust runtime configs without restarting engines.
3. Halt / resume trading per index or globally; manually exit a position.
4. Onboard or rotate Upstox credentials.
5. Drill into historical positions and option-chain analytics.

The frontend is a **dumb renderer** of backend-built view payloads. It never
computes trading state. It owns navigation, forms, and visualisation only.

---

## 1. Scope Split — 10a vs 10b

Phase 10 is split into two shippable slices.

### Phase 10a — Core Operator Dashboard

Uses **only existing backend endpoints** (Phase 9 surface). Nothing new on
the server side. Ships fully usable trading UI.

Pages:
- `/login`
- `/onboarding/credentials` (first-boot wizard)
- `/dashboard`
- `/positions` (unified active + closed-today + history in one table)
- `/reports/[id]`
- `/configs`
- `/operations` (manual exit, kill switch, instrument refresh, token request)

Exit criteria for 10a:
- Lighthouse desktop score ≥ 90 on `/dashboard`.
- WebSocket reconnects within 2 s of backend restart.
- Operator can complete: login → wizard → live ticks → halt index → manual
  exit → resume — without reaching for the terminal.
- All three themes render without layout shift.
- `pnpm lint`, `pnpm typecheck`, `pnpm test`, `pnpm build` all green.

### Phase 10b — Analytics + Snapshots

Adds the analytics surface. Requires modest backend work:

Backend additions (small):
- New tables: `metrics_option_chain_history`, `metrics_market_snapshots`.
- New scheduler job that buckets option-chain rollups every minute.
- New scheduler jobs that capture marker snapshots (pre-open, market-open,
  mid-session × 4, pre-close, EOD).
- New REST endpoints under `/analytics/*`.

Frontend additions:
- `/analytics` page with chart panel + customization rail + snapshot strip.

Exit criteria for 10b:
- All snapshot kinds appear in the strip on a real trading day.
- Chart panel switches between `pcr`, `oi_change`, `multi_strike_oi`, `max_pain`
  without flicker (uses `lightweight-charts`).
- Filter rail (date, granularity, index, overlays) updates the chart in
  ≤300 ms median.

---

## 2. Tech Stack (locked)

| Concern | Choice | Why |
|---|---|---|
| Framework | **Next.js 14** App Router, React 18, TypeScript strict | SSR for `/login`, SPA for everything else; first-class App Router file routing |
| Package manager | **pnpm** | Fast, deterministic; CI-friendly |
| Styling | **TailwindCSS** v3 + **shadcn/ui** | Tokens drive everything; copy-in components, no runtime overhead |
| Components | shadcn primitives + **Radix UI** | Accessibility built in |
| Icons | **lucide-react** | Consistent stroke width, free, tree-shakable |
| State | **Zustand** v4 | Tiny, no boilerplate, supports selectors |
| Data fetching | **fetch** + thin wrapper (no SWR/RQ) | One operator, low traffic — caching is overkill |
| WebSocket | Native `WebSocket` | Protocol is plain JSON; no Socket.IO |
| Forms | **react-hook-form** + **zod** | Schema validation matches the backend Pydantic shapes |
| Charts | **lightweight-charts** v5 (TradingView) | Built for financial overlays; small bundle; dark-first |
| Tables | **@tanstack/react-table** v8 | Headless; integrates with shadcn `Table` |
| Date handling | **date-fns** + **date-fns-tz** (Asia/Kolkata) | Backend uses ISO + IST offset; matches |
| Tests | **vitest** + **@testing-library/react** + **playwright** (e2e smoke) | Fast unit + integration |
| Lint / format | **eslint-config-next**, **prettier**, **prettier-plugin-tailwindcss** | Standard |

**Banned**: Material UI, Ant Design, Chakra, styled-components, emotion,
Socket.IO, Redux, Apollo, Zod-less forms, runtime CSS-in-JS, gradients,
glassmorphism, neon glows. We are building a serious tool, not a portfolio
piece.

---

## 3. Repository Layout

```
frontend/
├── app/
│   ├── (auth)/
│   │   ├── login/page.tsx
│   │   └── onboarding/credentials/page.tsx
│   ├── (operator)/                       # protected segment, AppShell
│   │   ├── layout.tsx
│   │   ├── dashboard/page.tsx
│   │   ├── positions/
│   │   │   ├── page.tsx
│   │   │   └── [id]/page.tsx             # redirect to /reports/[id]
│   │   ├── reports/[id]/page.tsx
│   │   ├── analytics/page.tsx            # 10b
│   │   ├── configs/page.tsx
│   │   └── operations/page.tsx
│   ├── api/                              # local Next API routes if needed
│   ├── layout.tsx                        # ThemeProvider, fonts, providers
│   ├── error.tsx
│   ├── not-found.tsx
│   └── globals.css
├── components/
│   ├── ui/                               # shadcn primitives (generated)
│   ├── shell/                            # AppShell, SideNav, TopBar, CommandMenu
│   ├── tables/                           # PositionsTable, ConfigsTable
│   ├── forms/                            # CredentialsForm, ConfigForm
│   ├── charts/                           # AnalyticsChart, PnlSparkline
│   ├── kpi/                              # StatTile, StatusPill, ModeBadge
│   ├── feedback/                         # Toast wrappers, BannerStack
│   └── overlays/                         # KillSwitchDialog, ManualExitDialog
├── lib/
│   ├── api/
│   │   ├── client.ts                     # fetch wrapper, JWT, errors
│   │   ├── endpoints.ts                  # typed endpoint helpers
│   │   └── schemas.ts                    # zod mirrors of backend payloads
│   ├── ws/
│   │   ├── connection.ts                 # singleton WS client
│   │   ├── protocol.ts                   # message types
│   │   └── reconnect.ts                  # backoff state machine
│   ├── stores/
│   │   ├── authStore.ts
│   │   ├── viewsStore.ts                 # one entry per view key
│   │   ├── themeStore.ts
│   │   └── uiStore.ts                    # sidebar collapsed, dialogs open
│   ├── theme/
│   │   ├── tokens.ts                     # exports CSS-var names typed
│   │   └── themes.ts                     # theme registration
│   ├── utils/
│   │   ├── format.ts                     # money, pct, time-ago
│   │   ├── ist.ts                        # Asia/Kolkata helpers
│   │   └── cn.ts                         # className merge
│   └── types/
│       ├── views.ts                      # mirrors of view payloads
│       ├── api.ts                        # mirrors of REST shapes
│       └── ws.ts
├── public/
│   ├── icons/
│   └── fonts/                            # self-hosted Inter + JetBrains Mono
├── styles/
│   └── themes/
│       ├── slate-dark.css
│       ├── carbon-dark.css
│       └── operator-light.css
├── tests/
│   ├── setup.ts
│   ├── unit/
│   └── e2e/
├── .env.example
├── next.config.mjs
├── tailwind.config.ts
├── postcss.config.js
├── tsconfig.json
├── eslint.config.mjs
├── prettier.config.mjs
├── package.json
└── pnpm-lock.yaml
```

---

## 4. Environment Variables (frontend)

```
NEXT_PUBLIC_API_BASE_URL=https://upstoxapipcrmomentum.com    # or http://<ec2-ip>:8000 in dev
NEXT_PUBLIC_WS_URL=wss://upstoxapipcrmomentum.com/stream     # or ws://<ec2-ip>:8000/stream in dev
NEXT_PUBLIC_DEFAULT_THEME=slate-dark
NEXT_PUBLIC_FEATURE_ANALYTICS=true                           # toggles 10b page
```

JWT lives in `sessionStorage` (key: `pcr.jwt`). It is **not** in `localStorage`
— short-lived browser sessions only.

---

## 5. Build & Run

### Dev (against EC2 backend)

```bash
cd frontend
pnpm install
cp .env.example .env.local
# edit .env.local to point NEXT_PUBLIC_API_BASE_URL at the EC2 host
pnpm dev    # http://localhost:3000
```

### Build

```bash
pnpm build
pnpm start
```

### Tests

```bash
pnpm test            # vitest
pnpm test:e2e        # playwright
pnpm typecheck       # tsc --noEmit
pnpm lint            # eslint
```

All four must be green before commit.

---

## 6. Acceptance Tests (manual, run before declaring 10a done)

1. **Cold start**: backend running, frontend up. Navigate to `/`. Should
   redirect to `/login`. Login with admin creds. Lands on `/dashboard`.
2. **First-boot wizard**: clear Upstox creds in DB. Login. Should redirect
   to `/onboarding/credentials`. Fill the form, save. Should redirect to
   `/dashboard` with degraded banner cleared after `auth_status=valid`.
3. **Live ticks**: open `/dashboard`. Per-index card should show live
   premium ticks during market hours.
4. **Halt index**: hit "Halt nifty50". Strategy state pill flips to
   "DISABLED". `system_event` notification toast appears.
5. **Resume index**: hit "Resume nifty50". Confirm dialog → reason field →
   submit. Pill flips back.
6. **Manual exit**: open a paper position. From `/positions`, click the row
   → "Manual exit" → reason → submit. Position transitions to EXIT_PENDING
   then CLOSED_MANUAL.
7. **Configs round-trip**: edit `risk.daily_loss_circuit_pct`. Save. Refresh
   page. Value persists.
8. **WS resilience**: kill the API gateway process on EC2. Banner appears
   ("Disconnected — reconnecting"). Restart gateway. Banner clears within
   2 s, full state replaces.
9. **Theme switch**: cycle through all three themes via the Command Palette
   (⌘K → "Switch theme"). No layout shift, no Flash of Unstyled Content.
10. **JWT expiry**: artificially set token TTL to 60 s. Wait for 30 s before
    expiry — frontend should silently refresh. After full expiry, all calls
    should redirect to `/login`.

---

## 7. Sibling Documents in This Folder

| File | Purpose |
|---|---|
| `00_Frontend_Plan.md` | This file (index + scope) |
| `01_Design_System.md` | Tokens, themes, typography, spacing, motion |
| `02_App_Shell.md` | Layout, side nav, top bar, command palette, providers |
| `03_Components.md` | Component inventory, prop contracts, usage rules |
| `04_Pages.md` | Every page in detail with section breakdowns |
| `05_State_and_Data.md` | Zustand stores, WS connection, REST client, JWT |
| `06_Charts_Analytics.md` | Analytics page (Phase 10b) full spec |
| `07_Implementation_Order.md` | Step-by-step build sequence |

Read them in order.

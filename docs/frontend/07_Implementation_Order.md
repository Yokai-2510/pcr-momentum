# Implementation Order — Step-by-Step

This is the build sequence. Follow it from top to bottom. Each step is
verifiable. Do not skip ahead.

> **Before you start**: read `docs/Frontend_Integration.md`, then the rest of the
> `docs/frontend/` folder, in order.

The frontend lives at `frontend/` inside the EC2 repo
(`/home/ubuntu/premium_diff_bot/repo/frontend`). All commands assume that
working directory unless stated otherwise.

---

## Step 0 — Cut a branch

```bash
ssh ... "cd /home/ubuntu/premium_diff_bot/repo && git checkout main && git pull && git checkout -b phase-10a-frontend-shell"
```

Mark the todo "Phase 10a in progress" in your scratchpad.

---

## Step 1 — Bootstrap Next.js

On EC2:

```bash
cd /home/ubuntu/premium_diff_bot/repo
pnpm create next-app frontend --typescript --tailwind --eslint --app --src-dir=false --import-alias "@/*" --use-pnpm
```

Hand-edit `frontend/package.json` to:
- Pin Node engine (`"engines": { "node": ">=20" }`).
- Add scripts: `lint`, `typecheck`, `test`, `test:e2e`, `format`.

Verify:

```bash
cd frontend
pnpm dev
```

Expected: Next.js welcome page on `:3000`. Stop the dev server.

Commit checkpoint: `feat(fe): bootstrap Next.js app`.

---

## Step 2 — Tailwind, shadcn, fonts

1. Replace `frontend/tailwind.config.ts` with the version in
   `docs/frontend/01_Design_System.md` §3.
2. Add the three theme CSS files to `frontend/styles/themes/`:
   `slate-dark.css`, `carbon-dark.css`, `operator-light.css`. Contents come
   from §2.1, §2.2, §2.3 of the same doc.
3. Update `frontend/app/globals.css` to import all three themes plus the
   base reset.
4. Initialize shadcn:

   ```bash
   cd frontend
   pnpm dlx shadcn@latest init
   # When prompted: TypeScript, App Router, slate base (we override anyway),
   # CSS variables, prefix none.
   ```

5. Generate primitives:

   ```bash
   pnpm dlx shadcn@latest add button card badge input label textarea select \
     checkbox switch dialog sheet popover tooltip dropdown-menu command \
     tabs table sonner skeleton separator scroll-area form alert \
     toggle toggle-group accordion calendar
   ```

6. Self-host fonts. Drop `InterVariable.woff2` and
   `JetBrainsMono-Variable.woff2` into `frontend/public/fonts/`. Wire them
   in `frontend/app/layout.tsx` using `next/font/local`, exporting
   `--font-sans` and `--font-mono`.

7. Add `lib/utils/cn.ts` (clsx + tailwind-merge).

Verify:

```bash
pnpm typecheck
pnpm lint
pnpm build
```

All three must succeed. Smoke-test the dev server: the welcome page should
now render in the default theme with the proper background colour.

Commit checkpoint: `feat(fe): tailwind tokens, shadcn primitives, fonts`.

---

## Step 3 — Stores + utilities

1. Implement Zustand stores per `05_State_and_Data.md` §2:
   - `lib/stores/authStore.ts`
   - `lib/stores/viewsStore.ts`
   - `lib/stores/themeStore.ts`
   - `lib/stores/uiStore.ts`
   - `lib/stores/commandStore.ts`
   - `lib/stores/index.ts` (exports all + `resetAllStores()` for tests)

2. Implement utilities:
   - `lib/utils/format.ts` (per §6 of the same doc)
   - `lib/utils/ist.ts`
   - `lib/utils/semanticColor.ts`

3. Wire `themeStore` to `<html data-theme>`. Place a tiny client component
   `ThemeBoot` in `app/layout.tsx` that reads `localStorage` and sets the
   attribute before first paint to avoid FOUC.

4. Unit-test format helpers with vitest. Add tests for INR grouping,
   percent signs, OI abbreviation. Aim ≥ 95 % coverage on `format.ts`.

Verify:

```bash
pnpm test
```

Commit checkpoint: `feat(fe): stores and utilities`.

---

## Step 4 — REST client + types

1. Implement `lib/api/client.ts` per §4.1.
2. Mirror types in `lib/types/views.ts`, `lib/types/api.ts` from
   `docs/Frontend_Basics.md` §5 and `docs/API.md`.
3. Implement `lib/api/endpoints.ts` per §4.2 (every helper, but stubbed
   bodies with `request<T>(...)` calls).
4. Add `lib/api/mockClient.ts` with fixtures for at least:
   - `/auth/login` (success + 401)
   - `/configs` (full bundle)
   - `/positions/open` (one CE + one PE)
   - `/positions/closed_today` (a few rows)
   - `/health` (green)

5. Add an `if (process.env.NEXT_PUBLIC_API_BASE_URL === '__mock__') return mock(...)`
   short-circuit at the top of `request()`.

Verify: write a contract test that calls each endpoint helper against the
mock client and asserts the response is typed.

Commit checkpoint: `feat(fe): typed REST client and mock fixtures`.

---

## Step 5 — WebSocket layer

1. Implement `lib/ws/protocol.ts` (message type unions).
2. Implement `lib/ws/connection.ts` (singleton WSClient).
3. Implement `lib/ws/reconnect.ts` (backoff state machine).
4. Implement `lib/ws/useWSConnection.ts` hook that mounts the singleton
   exactly once.
5. Wire `WSClient.onMessage` to push:
   - `snapshot` → `viewsStore.set` for each key in `data`.
   - `update` → `viewsStore.set(view, data)`.
   - `notification` → `toast`.
   - `ping` → `pong` reply.

6. Add a `WSConnectionState` reader (`useWSState()`) for the GlobalStatus
   component.

Verify: wire a tiny test page (in `app/(dev)/ws-debug/page.tsx`) that
shows raw view payloads via `useViewsStore.getState().byKey`. Hit the EC2
backend in dev (`NEXT_PUBLIC_WS_URL=ws://3.6.128.21:8000/stream` after
opening port). After login (next steps), confirm the snapshot arrives.

Commit checkpoint: `feat(fe): websocket connection and stores`.

---

## Step 6 — Auth (login + bootstrap + refresh)

1. Implement `lib/auth/jwt.ts` with `decodeJwtPayload(token)`.
2. Implement `lib/auth/bootstrap.ts` for server components (cookie read).
3. Implement `lib/auth/clientBootstrap.ts` for client (sessionStorage read).
4. Schedule refresh in `authStore.setSession`.
5. Build `app/(auth)/login/page.tsx` with `LoginForm` component
   (`components/forms/LoginForm.tsx`).
6. Add a route guard for `(operator)` segment in
   `app/(operator)/layout.tsx` (per `02_App_Shell.md` §10.2).

Verify: navigate `/`. Should redirect to `/login`. Submit valid creds.
Should redirect to `/dashboard` (which still renders a stub).

Commit checkpoint: `feat(fe): auth flow with login + JWT refresh`.

---

## Step 7 — App Shell

1. Build `components/shell/AppShell.tsx`, `SideNav.tsx`, `NavItem.tsx`,
   `TopBar.tsx`, `EnvBadge.tsx`, `ModePill.tsx`, `GlobalStatus.tsx`,
   `ThemeMenu.tsx`, `UserMenu.tsx`, `BannerStack.tsx`, `Banner.tsx`,
   `CommandMenu.tsx`. Specs in `02_App_Shell.md`.
2. Wire keyboard shortcuts globally via a single
   `lib/hooks/useGlobalShortcuts.ts`.
3. Add the engine-status footer in SideNav using `viewsStore.health`.

Verify: visit `/dashboard`. The shell renders. ⌘K opens the command menu.
ThemeMenu cycles all three themes without FOUC. Sidebar collapse
animation is 200 ms.

Commit checkpoint: `feat(fe): app shell, side nav, top bar, command menu`.

---

## Step 8 — Onboarding wizard

Build `app/(auth)/onboarding/credentials/page.tsx` and
`components/forms/CredentialsForm.tsx` per `04_Pages.md` §2.

Verify with mock:
- Step 1 form submits and shows Step 2.
- Step 2 button calls `commandsApi.upstoxTokenRequest` (mocked) and renders
  the message + URL.
- Step 3 polls `credentialsApi.get()` (mocked) and transitions to success
  when the mock returns `valid` after 2 polls.

Commit checkpoint: `feat(fe): onboarding credentials wizard`.

---

## Step 9 — Dashboard

1. Build `app/(operator)/dashboard/page.tsx` per `04_Pages.md` §3.
2. Build supporting components: `components/kpi/StatTile.tsx`,
   `StatusPill.tsx`, `ConnectionDot.tsx`, plus `IndexSummaryCard.tsx`,
   `HealthStrip.tsx`, `PnlSparkline.tsx` (uses `lightweight-charts`).
3. Wire all live data via `useViewsStore`.

Verify (against EC2 backend, market hours or replay):
- KPI tiles populate within 1 s of WS open.
- A live tick flashes the live premium / PnL fields.
- "Halt nifty50" → strategy state pill flips to DISABLED.

Commit checkpoint: `feat(fe): dashboard page with live KPI + per-index cards`.

---

## Step 10 — Positions page (unified)

1. Build `components/tables/DataTable.tsx`, `columns.ts`,
   `PositionsTable.tsx` per `03_Components.md` §4.
2. Build `app/(operator)/positions/page.tsx` per `04_Pages.md` §4.
3. URL-sync the filters (`?status=&index=&mode=&from=&to=&page=`).

Verify:
- Active scope updates premium/PnL within 200 ms of WS update.
- Historical scope paginates correctly via API.
- Manual exit button only enabled on OPEN rows.

Commit checkpoint: `feat(fe): unified positions page (active + closed + historical)`.

---

## Step 11 — Reports page

Build `app/(operator)/reports/[id]/page.tsx` per `04_Pages.md` §5.
Sub-components: `PremiumTraceChart.tsx`, `JsonPreview.tsx`.

Verify: open a closed-position id and confirm summary, order chain table,
and JSON preview render correctly.

Commit checkpoint: `feat(fe): position report page`.

---

## Step 12 — Configs page

1. Define `components/forms/configFieldSpecs.ts` per `04_Pages.md` §6.
2. Build `components/forms/ConfigForm.tsx` (generic, schema-driven).
3. Build `app/(operator)/configs/page.tsx`.

Verify: changing `risk.daily_loss_circuit_pct` and saving updates the
backend (verify Redis key and DB row), and the Audit Trail row appears
(if enabled).

Commit checkpoint: `feat(fe): configs page with section forms`.

---

## Step 13 — Operations page

Build `app/(operator)/operations/page.tsx` per `04_Pages.md` §7.
Includes `KillSwitchSheet`, `GlobalKillDialog`, `GlobalResumeDialog`,
`ConfirmDialog`.

Verify all action buttons against the live backend (or mock).

Commit checkpoint: `feat(fe): operations page`.

---

## Step 14 — Banners + degraded modes

Wire `BannerStack` to read `dashboard.system_state.trading_disabled_reason`
from the views store and emit banners per `02_App_Shell.md` §5.3.

Verify by manually toggling backend Redis flags:
- `system:flags:trading_disabled_reason = "manual_kill"` → destructive banner.
- `system:flags:trading_disabled_reason = "awaiting_credentials"` → warning banner.

Commit checkpoint: `feat(fe): banner stack with degraded-mode reasons`.

---

## Step 15 — Polish + accessibility

1. Run `pnpm lint -- --max-warnings 0`.
2. Run `pnpm typecheck`.
3. Run a Lighthouse desktop audit on `/dashboard` against `pnpm build && pnpm start`.
   Target: Performance ≥ 90, Accessibility ≥ 95, Best Practices ≥ 95.
4. Run axe-core on every page. Fix every violation.
5. Verify all three themes against WCAG AA contrast.
6. Tab-only navigation: complete dashboard happy-path with keyboard alone.

Commit checkpoint: `chore(fe): a11y, lint, lighthouse cleanups`.

---

## Step 16 — E2E smoke

Implement Playwright tests:

- `tests/e2e/login.spec.ts` — login redirect + happy path.
- `tests/e2e/dashboard.spec.ts` — load dashboard, see at least one KPI
  populate (against mock backend).
- `tests/e2e/positions.spec.ts` — switch tabs, paginate.
- `tests/e2e/configs.spec.ts` — save a section.
- `tests/e2e/operations.spec.ts` — open kill-switch sheet.

Run:

```bash
pnpm test:e2e
```

Commit checkpoint: `test(fe): playwright e2e smoke suite`.

---

## Step 17 — Push and merge

```bash
ssh ... "cd /home/ubuntu/premium_diff_bot/repo && git push -u origin phase-10a-frontend-shell"
```

Open a PR (or fast-forward main if your workflow allows):

```bash
ssh ... "cd /home/ubuntu/premium_diff_bot/repo && git checkout main && git merge --ff-only phase-10a-frontend-shell && git push origin main"
```

Update `docs/Project_Plan.md` Phase 10a status to "Complete" using the
patch pattern in `docs/Frontend_Integration.md` §8.

---

## Step 18 — Phase 10b (Analytics)

Only after 10a is shipped and stable.

### 18.1 Backend additions

1. Migration: add `metrics_option_chain_history` and
   `metrics_market_snapshots` tables (see `docs/Schema.md` updates from this
   PR).
2. Background engine job: bucket option-chain rollups every 60 s into the
   new table.
3. Scheduler jobs: capture snapshots at the eight time markers.
4. New REST endpoints: `/analytics/option_chain/{index}`,
   `/analytics/snapshots/{index}`, `/analytics/strategy/{index}`. See
   `docs/API.md` Phase 10b section.
5. Tests: contract tests in `backend/tests/api/test_analytics.py` against
   in-memory fakes.

### 18.2 Frontend page

1. `app/(operator)/analytics/page.tsx`.
2. `components/charts/AnalyticsChart.tsx` and supporting series builders
   per `06_Charts_Analytics.md`.
3. `SnapshotCard`, `StrategyHeatmap`, customisation rail.
4. URL state via `useUrlState`.
5. Add a `useAnalyticsStore` (Zustand) for the rail's local state.

Verify all six metric tabs render against canned data and against live
backend on a real trading day.

Commit checkpoint: `feat(fe,be): phase 10b analytics page + endpoints`.

---

## Step 19 — Final acceptance

Run the manual UAT script in `00_Frontend_Plan.md` §6 against the EC2
deployment. Mark Phase 10 complete in `Project_Plan.md`. Update the
README's status table.

---

## Cheatsheet — When You Get Stuck

- **Component looks wrong but tokens are right** → check `data-theme` on
  `<html>`. Probably FOUC.
- **WS keeps reconnecting** → check `wsUrl` env, JWT expiry, server logs.
- **Type error in `lib/types/views.ts`** → backend payload changed; update
  the doc first, then the type.
- **Tailwind class doesn't apply** → confirm it's in `content` glob in
  `tailwind.config.ts`.
- **Hydration mismatch** → some date/time used `Date.now()` outside an
  effect; replace with deterministic formatter or move into `useEffect`.
- **PowerShell ate your heredoc** → `scp` the file instead. See
  `docs/Frontend_Integration.md`.

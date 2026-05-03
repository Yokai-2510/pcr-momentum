# Pages — Detailed Specs

Every page, section by section, with components used, data sources,
loading/empty/error states, and acceptance criteria. Specs use the
component names from `03_Components.md` and the data shapes from
`docs/Frontend_Basics.md` and `docs/API.md`.

---

## 1. `/login`

### Layout
Full-bleed centered card on a `bg-background` page. No app shell.

### Sections

- **Hero**: wordmark `pcr-momentum` (`text-h-xl font-mono`) above a one-line
  tagline ("Single-operator premium-diff trading console").
- **Card** (`max-w-sm`):
  - `<LoginForm onSuccess={handleLogin}>`
  - Below the form: a small "Forgot password? Contact administrator." line
    in `text-ui-xs text-muted-foreground`.

### Data
- `POST /auth/login` (`LoginRequest`) → `{ token, expires_at, user }`.
- On success: store JWT (sessionStorage), router-push `/dashboard`.
- On error 401: form-level error "Invalid username or password" (no field-
  level error).

### States
- **Loading**: button shows spinner inside; inputs disabled.
- **Error**: inline alert above the form, tone `destructive`.
- **Empty**: n/a.

### Acceptance
- Submitting empty fields shows zod validation errors.
- After successful login, the next page load skips `/login` (cookie/session
  set).

---

## 2. `/onboarding/credentials`

### Layout
Centered card (`max-w-2xl`), no app shell. Visual hierarchy:

```
┌── stepper (3 steps): Credentials → Token → Verify ──┐
│                                                       │
│  Step 1: Upstox API credentials                       │
│  ──────────────────────────────                       │
│  <CredentialsForm>                                    │
│                                                       │
│  Step 2: Request access token                         │
│  ──────────────────────────                           │
│  Button: "Send token request" + status display        │
│                                                       │
│  Step 3: Verify auth                                  │
│  ─────────────────                                    │
│  Live status pill, refreshes every 3 s                │
│                                                       │
└───────────────────────────────────────────────────────┘
```

### Data flow
- Step 1: `GET /credentials/upstox` to detect existing creds (masked) → if
  `configured`, prefill non-secret fields. Submit → `POST /credentials/upstox`.
- Step 2: `POST /commands/upstox_token_request` → returns
  `{ authorization_expiry, notifier_url, message }`. Display the message
  prominently and a copyable WhatsApp/notifier URL.
- Step 3: poll `GET /credentials/upstox` every 3 s. When `auth_status === "valid"`,
  show success and CTA "Go to dashboard". When `invalid`, show a retry button
  for Step 2.

### Components
- `Stepper` (custom, lightweight — three `Badge`s connected by a 1-px line).
- `CredentialsForm`.
- `Card` per step.
- `EmptyState` for "no creds configured" between steps if needed.

### Acceptance
- Pressing back at Step 2 returns to Step 1 with the form values intact.
- Success state navigates to `/dashboard` after 1 s delay.
- All Step-2 polls visible in network panel; cancelled on unmount.

---

## 3. `/dashboard`

### Layout

```
┌── Page header: "Dashboard"  ────────────── [Last updated: 2s ago] ┐
│                                                                    │
│ ┌── KPI Row ─────────────────────────────────────────────────────┐│
│ │ [Total PnL today (KPI hero)] [Realized] [Unrealized] [Trades] ││
│ │                              [Open positions] [Win rate]       ││
│ └────────────────────────────────────────────────────────────────┘│
│                                                                    │
│ ┌── Per-Index Cards (2 cols on lg, 1 on md) ─────────────────────┐│
│ │ ┌── nifty50 ──────────────┐  ┌── banknifty ─────────────────┐ ││
│ │ │ State pill, ATM, basket  │  │ State pill, ATM, basket     │ ││
│ │ │ PnL today, open premium  │  │ PnL today, open premium     │ ││
│ │ │ ΔPCR mini, mode pill     │  │ ΔPCR mini, mode pill        │ ││
│ │ │ Active position card     │  │ Active position card        │ ││
│ │ └──────────────────────────┘  └─────────────────────────────┘ ││
│ └────────────────────────────────────────────────────────────────┘│
│                                                                    │
│ ┌── Health strip ────────────────────────────────────────────────┐│
│ │ summary pill │ engines grid (5 dots) │ deps (Redis/PG/Broker) ││
│ └────────────────────────────────────────────────────────────────┘│
│                                                                    │
│ ┌── PnL sparkline (last 30 min) ─────────────────────────────────┐│
│ └────────────────────────────────────────────────────────────────┘│
└────────────────────────────────────────────────────────────────────┘
```

### Sections

#### 3.1 KPI Row
- 6 `StatTile`s in a `grid-cols-6 gap-4` (collapses to 3 on `md`, 2 on `sm`).
- The first tile uses `size="lg"` and `tone` switches between `success` and
  `destructive` based on PnL sign.
- Sources:
  - **Total PnL today** — `dashboard.total_pnl_today`
  - **Realized** — `pnl.realized_today`
  - **Unrealized** — `pnl.unrealized`
  - **Trades** — `dashboard.trades_today`
  - **Open positions** — `dashboard.open_positions_count`
  - **Win rate** — `dashboard.win_rate_today` × 100, rendered as Pct

All values use `LiveValue` with default flash class.

#### 3.2 Per-Index Card

`IndexSummaryCard` component:

```ts
type IndexSummaryCardProps = {
  index: "nifty50" | "banknifty";
};
```

Content:
- Header row: index label (mono), `StatusPill` for state, `ModeBadge`,
  small "Halt" / "Resume" `Button` (opens confirm dialog).
- 2-column metrics grid:
  - ATM strike, lot size
  - Today's PnL (Money), open premium (Premium)
  - Open dominance score (latest), reversal score
  - ΔPCR live mini-bar (interval value, with up/down arrow)
- Active position panel (only if `position:{index}` is non-null):
  - Strike (Strike), Side pill (CE/PE), Qty
  - Entry premium → Live premium (Premium each side, arrow between)
  - Running PnL (Money with sign)
  - Hold time, Action button "Manual exit" (opens dialog)
- Empty (no active position): `EmptyState` with "FLAT" badge and last
  outcome ("Last trade: TGT @ 11:32, +₹2,140").

Data:
- `dashboard.indexes[index]`
- `position:{index}` (live)
- `strategy:{index}`
- `delta_pcr:{index}.interval`

#### 3.3 Health Strip
Horizontal card with three clusters:
- Summary pill ("OK" / "DEGRADED" / "DOWN") sourced from `health.summary`
- Engine dots (5 of them) sourced from `health.engines`
- Dependency pills (Redis / Postgres / Broker) sourced from `health.dependencies`

Click any cluster → routes to `/operations?focus=health`.

#### 3.4 PnL Sparkline
- `PnlSparkline` with last 60 minutes of `pnl.live` history (server-pushed).
- 200 px tall, full width.
- Tooltip shows ts + PnL on hover.

### States
- **Loading**: skeleton tiles + skeleton cards. Live data populates within
  ≤ 1 s of WS snapshot.
- **Empty**: never empty if backend healthy. If WS snapshot is missing,
  show banner "Connecting to live data…" + skeletons.
- **Error**: `BannerStack` shows the relevant degraded mode banners.

### Acceptance
- Each KPI value flashes once when changed.
- Halt → confirm dialog → state pill flips to DISABLED within 500 ms.
- Health strip dot turns red within 30 s of an engine going down (driven by
  `last_hb_ts` math).

---

## 4. `/positions`

### Layout

```
┌── Page header: "Positions" ──────────────────────── [Filter chips] ┐
│ Status: [Active] [Closed today] [All historical]                    │
│ Index:  [All] [nifty50] [banknifty]                                 │
│ Mode:   [All] [paper] [live]                                        │
│ Date:   [from] [to]   (only when historical scope)                  │
│                                                                      │
│ ┌── Tabs (controls scope, mirrors Status filter) ──────────────────┐│
│ │ [Active]  [Closed today]  [Historical]                           ││
│ └──────────────────────────────────────────────────────────────────┘│
│                                                                      │
│ <PositionsTable density="compact" sortable pagination={...}>         │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

### Tabs / scope

Three scopes, each backed by a different data source:

| Scope | Source | Live? |
|---|---|---|
| **Active** | `GET /positions/open` + WS `position:{index}` for live premium / PnL | yes |
| **Closed today** | `GET /positions/closed_today` | no |
| **Historical** | `GET /positions/history?from&to&index&mode&page&page_size&sort` | no |

The Status filter chip and the Tab share state — they are two views of the
same selector.

### Table

- Columns: see `03_Components.md` §4 (PositionsTable).
- In **Active** scope, the live premium and PnL columns subscribe to
  `position:{index}` and update via `useViewsStore`. The row is **highlighted**
  with `rowAccent="warning"` when status is `EXIT_PENDING`.
- Row click → opens a `Sheet` from the right with full position detail (same
  content as `/reports/[id]` but inline).

### Pagination
- Default page size 50; selector for 25 / 50 / 100 / 200.
- Server-side pagination — uses `total` from API response.

### Bulk actions (future)
None in 10a. The placeholder header is reserved.

### States
- **Empty Active**: `EmptyState` with `Briefcase` icon, "No open positions",
  no CTA.
- **Empty Historical**: "No positions match these filters" + "Reset filters" CTA.
- **Loading**: 8 skeleton rows.

### Acceptance
- Active scope updates premium/PnL within 200 ms of a tick.
- Historical pagination preserves filter state in URL query string.
- "Manual exit" action only renders for OPEN rows; greyed and tooltipped
  for everything else.

---

## 5. `/reports/[id]`

### Layout

```
┌── Page header ────────────────────────────────────────────────────┐
│ ← Back   Position #abc123   StatusPill   ModeBadge                │
└────────────────────────────────────────────────────────────────────┘

┌── Summary card ──────┐ ┌── PnL trace mini-chart ──┐
│ Index, Side, Strike  │ │  premium over time, with │
│ Qty, Lots            │ │  entry/SL/TGT marker     │
│ Entry @ , Exit @     │ │                          │
│ Hold, PnL (large)    │ │                          │
└──────────────────────┘ └──────────────────────────┘

┌── Reasons ─────────────────────────────────────────────────────────┐
│ Entry reason (text), Exit reason (text)                            │
│ Premium-diff dominance score, ΔPCR snapshot at entry               │
└────────────────────────────────────────────────────────────────────┘

┌── Order chain (tabular) ──────────────────────────────────────────┐
│ ts | side | order_id | status | qty | px | retries | notes        │
└────────────────────────────────────────────────────────────────────┘

┌── Raw payload (collapsible JSON) ─────────────────────────────────┐
└────────────────────────────────────────────────────────────────────┘
```

### Data
- `GET /reports/{position_id}` returns the closed-position row enriched with
  `coerce_jsonb` payloads: order chain, exit_reason, fill chain, ΔPCR sample.

### Components
- `Card` × 3
- `PremiumTraceChart` (lightweight-charts mini chart, internal to this page)
- `DataTable` for order chain
- `JsonPreview` (`<pre>` with Tailwind monospace)

### Acceptance
- 404 if position not found, with "Back to positions" CTA.
- Copy-to-clipboard buttons on `position_id` and `order_id`.
- The mini-chart renders without flicker even when premium series has < 5
  points.

---

## 6. `/configs`

### Layout

```
┌── Page header: "Configuration" ─────────────────────────────────────┐
│                                                                      │
│ ┌── Tabs ──────────────────────────────────────────────────────────┐│
│ │ [Risk] [Execution] [Session] [nifty50] [banknifty]               ││
│ └──────────────────────────────────────────────────────────────────┘│
│                                                                      │
│ ┌── Tab content ───────────────────────────────────────────────────┐│
│ │ <ConfigForm section="risk">                                       ││
│ │ Form fields rendered from configFieldSpecs[section]              ││
│ │                                                                   ││
│ │ Footer: [Reset] [Save]                                            ││
│ └──────────────────────────────────────────────────────────────────┘│
│                                                                      │
│ ┌── Audit trail (last 10 changes) ─────────────────────────────────┐│
│ │ ts | section | key | old → new | by                              ││
│ └──────────────────────────────────────────────────────────────────┘│
└──────────────────────────────────────────────────────────────────────┘
```

### Data
- Read: `GET /configs/{section}` on tab activation.
- Write: `PUT /configs/{section}` with the full validated payload.
- Audit trail (10b enhancement): if backend exposes one, render; otherwise
  hide the section.

### Sections

#### Risk
Fields: `daily_loss_circuit_pct` (0–1, percent), `max_concurrent_positions`
(int 1–10), `trading_capital_inr` (integer ≥ 0).

#### Execution
Per `state/schemas/config.ExecutionConfig`. Fields including
`buffer_inr`, `eod_buffer_inr`, `spread_skip_pct`, `drift_threshold_inr`,
`chase_ceiling_inr`, `open_timeout_sec`, `partial_grace_sec`,
`max_retries`, `worker_pool_size`, `liquidity_exit_suppress_after`
(time-hhmm).

#### Session
Fields: `market_open` (time-hhmm).

#### nifty50 / banknifty
`IndexConfig` fields per `04_Pages.md` §3.2 source.

### States
- **Dirty**: footer Save button is `disabled` until form is dirty (RHF).
- **Saving**: button spinner; inputs disabled.
- **Validation error**: inline messages from zod; toast on schema error.
- **Server error**: toast with `error.code`; form keeps dirty state.

### Acceptance
- Saving a section publishes to Redis and republishes `view:configs` —
  verify by watching the WS frame.
- Reset button reverts to last-known server state without a refetch.

---

## 7. `/operations`

### Layout

```
┌── Page header: "Operations" ───────────────────────────────────────┐
│                                                                      │
│ ┌── Trading control ───────────────────────────────────────────────┐│
│ │  Per-index halt/resume                                            ││
│ │  Global kill / resume (with confirm)                              ││
│ │  Daily loss circuit reset                                         ││
│ └──────────────────────────────────────────────────────────────────┘│
│                                                                      │
│ ┌── Manual exit ───────────────────────────────────────────────────┐│
│ │  Position picker (only OPEN positions)                            ││
│ │  Reason field                                                     ││
│ │  Submit                                                           ││
│ └──────────────────────────────────────────────────────────────────┘│
│                                                                      │
│ ┌── Broker / Auth ─────────────────────────────────────────────────┐│
│ │  Token request (button)                                           ││
│ │  Auth status                                                      ││
│ │  Capital funds (refresh)                                          ││
│ │  Kill-switch segments                                             ││
│ └──────────────────────────────────────────────────────────────────┘│
│                                                                      │
│ ┌── Maintenance ───────────────────────────────────────────────────┐│
│ │  Instrument refresh (button + per-index status)                   ││
│ │  Health diagnostics (calls /health/dependencies/test)             ││
│ └──────────────────────────────────────────────────────────────────┘│
└──────────────────────────────────────────────────────────────────────┘
```

### Behaviour
- Each section is a `Card`. Inputs and buttons are disabled when
  `trading_active` flips off.
- `?focus=health` query param scrolls to and highlights the Maintenance
  card.
- All destructive actions (`Global kill`, kill-switch toggle on, instrument
  refresh during market hours) prompt `ConfirmDialog`.

### Acceptance
- Halting nifty50 here is interchangeable with the same action from the
  Command palette and the Dashboard per-index card.
- After "Refresh instruments", the per-index status pill turns yellow
  ("running") then green ("ok @ 11:42") when the response arrives.

---

## 8. `/analytics` (Phase 10b)

Full spec in `06_Charts_Analytics.md`. Brief layout reference here:

```
┌── Page header: "Analytics" ─────────────────────────── Index pick ─┐
│ ┌── Metric tabs ────────────────────────────────────────────────┐ │
│ │ [PCR] [OI Change] [MultiStrike OI] [Max Pain] [ΔPCR] [Premium]│ │
│ └───────────────────────────────────────────────────────────────┘ │
│                                                                     │
│ ┌── Customization rail (right, w-72) ──┐ ┌── Chart panel ────────┐│
│ │ Date range, granularity, overlays,    │ │ AnalyticsChart        ││
│ │ strikes selector, theme               │ │ (lightweight-charts)  ││
│ └───────────────────────────────────────┘ └───────────────────────┘│
│                                                                     │
│ ┌── Snapshot strip (horizontal scroll) ─────────────────────────┐ │
│ │ pre_open │ market_open │ mid_session_1..4 │ pre_close │ eod   │ │
│ │ Each: SnapshotCard with summary metrics                       │ │
│ └───────────────────────────────────────────────────────────────┘ │
│                                                                     │
│ ┌── Strategy stats (cards row) ─────────────────────────────────┐ │
│ │ Entries, Win rate, Avg PnL, Reversal rate, Time-of-day heatmap│ │
│ └───────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 9. Errors / 404

### `app/error.tsx`
Top-level error boundary. Renders `ErrorState` with retry.

### `app/not-found.tsx`
Centered, `EmptyState` with "Page not found" + button to `/dashboard`.

---

## 10. Print / Export

Reports page (`/reports/[id]`) supports browser print:
- A `print:` Tailwind variant adjusts layout: hide nav, expand cards, force
  light theme via inline `<style media="print">` overriding `--background`,
  `--foreground` to print-friendly values.

CSV export buttons appear on:
- `/positions` (historical scope only)
- `/configs` audit trail
- `/analytics` chart data

Implementation: client-side CSV builder from the visible/filtered dataset.

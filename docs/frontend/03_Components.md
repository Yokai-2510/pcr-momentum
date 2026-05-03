# Component Inventory

Every component in the app, grouped by domain. Names are normative — use
exactly these names. Each entry lists:

- **Where it lives** (file path)
- **Props** (TypeScript signature)
- **Usage** (which page / context)
- **Composition** (which shadcn primitive(s) it wraps)
- **Acceptance** (what "done" looks like)

Don't invent new components without first checking this list. If something
similar exists, extend it. New entries must land here in the same PR.

---

## 1. Foundation (shadcn primitives — generated)

These are produced by `pnpm dlx shadcn@latest add <name>`. **Do not modify**
them by hand except to bind to our token names. Any per-app variant goes in
`components/<domain>/`, not in `components/ui/`.

Required primitives (run `add` for each):

```
button card badge input label textarea select checkbox switch
dialog sheet popover tooltip dropdown-menu command tabs table
toast (sonner) skeleton separator scroll-area form alert
toggle toggle-group accordion calendar
```

---

## 2. KPI / Status

### `StatTile`

```ts
type StatTileProps = {
  label: string;            // "Total PnL Today"
  value: ReactNode;         // formatted number or chip
  delta?: ReactNode;        // optional secondary line, e.g. "+0.71% vs yday"
  icon?: LucideIcon;
  tone?: "neutral" | "success" | "destructive" | "warning" | "info";
  size?: "default" | "lg";  // lg used for hero "Total PnL today"
  flashOnChange?: boolean;  // default true: 600ms bg flash on value change
};
```

- **Where**: `components/kpi/StatTile.tsx`
- **Composition**: `Card` + `CardHeader` (label + icon) + `CardContent` (value + delta)
- **Tone**: applies a 1-px left border accent (`border-l-4 border-l-{tone}`)
  on `lg`; on `default` only the value text colours.

### `StatusPill`

```ts
type StatusPillProps = {
  status: "OPEN" | "EXIT_PENDING" | "CLOSED_TGT" | "CLOSED_SL"
        | "CLOSED_REVERSAL" | "CLOSED_TIME" | "CLOSED_MANUAL"
        | "FLAT" | "ARMED" | "IN_CE" | "IN_PE" | "DISABLED";
  size?: "sm" | "md";
};
```

- **Where**: `components/kpi/StatusPill.tsx`
- **Composition**: `Badge`
- **Mapping**: pulls from `lib/utils/semanticColor.ts`.

### `ModeBadge`

```ts
type ModeBadgeProps = { mode: "paper" | "live" };
```

`paper` → `bg-info/15 text-info`, `live` → `bg-success/15 text-success`.

### `ConnectionDot`

```ts
type ConnectionDotProps = { state: "connected" | "stale" | "disconnected"; pulse?: boolean };
```

8 px dot. `connected` solid, `stale` solid amber, `disconnected` outline
red with `animate-pulse-soft` when `pulse`.

---

## 3. Numbers

### `Money`

```ts
type MoneyProps = {
  value: number;        // INR
  showSign?: boolean;   // default false; true for deltas
  precision?: number;   // default 2
  className?: string;
};
```

Renders `formatINR(value)` in `font-mono`. If `showSign`, applies
`text-success` when positive, `text-destructive` when negative.

### `Pct`

```ts
type PctProps = { value: number; precision?: number; showSign?: boolean };
```

### `Premium`

```ts
type PremiumProps = { value: number; precision?: number };
```

Plain mono, no currency prefix.

### `Strike`

```ts
type StrikeProps = { value: number };
```

Renders integer with thousands separator in mono.

### `OI`

```ts
type OIProps = { value: number };
```

Abbreviates ≥ 1 lakh (`12.4L`, `1.2Cr`); below that, integer with grouping.

### `LiveValue`

```ts
type LiveValueProps<T> = {
  value: T;
  render: (v: T) => ReactNode;
  flashClass?: string;   // default `bg-success/15`
};
```

Wraps any displayed live value with the 600 ms flash on change. Uses
`useFlashOnChange` hook.

---

## 4. Tables

### `DataTable<T>`

```ts
type DataTableProps<T> = {
  columns: ColumnDef<T>[];          // tanstack
  data: T[];
  density?: "default" | "compact";
  empty?: ReactNode;
  loading?: boolean;                // renders Skeleton rows
  onRowClick?: (row: T) => void;
  rowAccent?: (row: T) => "success" | "destructive" | "warning" | undefined;
  sortable?: boolean;               // wires up TanStack sort
  pagination?: { page: number; pageSize: number; total: number; onPageChange: (p: number) => void };
};
```

- **Where**: `components/tables/DataTable.tsx`
- **Composition**: shadcn `Table` + TanStack hooks
- **Behaviour**:
  - Sticky header on vertical scroll.
  - Loading: 8 skeleton rows matching column widths.
  - Empty: `EmptyState`.
  - Row accent applies a left 2-px border in the row's tone.

### `ColumnHelpers`

Helpers exported from `components/tables/columns.ts`:

```ts
moneyColumn(label, accessor, opts?): ColumnDef<T>
pctColumn(label, accessor, opts?): ColumnDef<T>
strikeColumn(label, accessor): ColumnDef<T>
timestampColumn(label, accessor): ColumnDef<T>
statusColumn(label, accessor): ColumnDef<T>
indexColumn(label, accessor): ColumnDef<T>     // renders nifty50/banknifty pill
modeColumn(label, accessor): ColumnDef<T>      // ModeBadge
actionsColumn<T>(buttons: (row: T) => ReactNode): ColumnDef<T>
```

Pages compose tables from these helpers. Avoid hand-writing `cell:`
renders inline — keeps tables visually consistent.

### `PositionsTable`

Specific composition for `/positions`. Columns:

| Col | Helper |
|---|---|
| Status | `statusColumn` |
| Index | `indexColumn` |
| Side (CE/PE) | inline pill |
| Mode | `modeColumn` |
| Strike | `strikeColumn` |
| Qty | mono integer |
| Entry @ | `timestampColumn` |
| Entry premium | `Premium` |
| Live / Exit premium | `Premium` (live updates while OPEN) |
| PnL | `moneyColumn` with sign |
| Hold | duration "14m 23s" |
| Reason | text |
| Actions | `actionsColumn` (Manual exit if OPEN; Report otherwise) |

Filter chips above the table (built-in):
- Status (multi: OPEN / CLOSED_*)
- Index (single: All / nifty50 / banknifty)
- Mode (single: All / paper / live)
- Date range (only enabled when status filter excludes OPEN)

### `ConfigsTable`

Used inside `/configs` for `risk` and `execution` sections (read-only
overview). Two columns: Key, Value. Edit happens in `ConfigForm`.

---

## 5. Forms

All forms use `react-hook-form` + `zod` + shadcn `Form`. Schemas mirror
backend Pydantic shapes; types come from `lib/types/api.ts`.

### `LoginForm`

```ts
type LoginFormProps = { onSuccess: (token: string) => void };
```

Fields: `username` (required), `password` (required, min 1).

### `CredentialsForm`

```ts
type CredentialsFormProps = {
  initial?: Partial<UpstoxCredentials>;  // existing creds (masked) for edit
  onSubmit: (values: UpstoxCredentials) => Promise<void>;
};
```

Fields per `API.md` §3.5:

| Field | Type | Notes |
|---|---|---|
| `api_key` | text | required |
| `api_secret` | password | required, never prefilled |
| `redirect_uri` | url | required, validated against backend's notifier URL |
| `totp_secret` | password | required |
| `mobile_no` | tel | min 10 |
| `pin` | password | min 4 |
| `analytics_token` | password | optional |
| `sandbox_token` | password | optional |

Layout: two-column on `md:`, single-column on mobile. Submit button at
bottom, full-width on mobile.

### `ConfigForm` (per section)

Generated from a section schema. Section types: `execution`, `session`,
`risk`, `index:nifty50`, `index:banknifty`. Each field carries:

```ts
type FieldSpec = {
  key: string;                 // path in payload
  label: string;
  hint?: string;
  unit?: string;               // e.g. "INR", "sec", "%"
  type: "number" | "integer" | "percent" | "time-hhmm" | "boolean";
  min?: number; max?: number; step?: number;
};
```

Field specs live in `components/forms/configFieldSpecs.ts` keyed by
section. Saving calls `PUT /configs/{section}` with the full section
payload.

### `ManualExitDialog`

```ts
type ManualExitDialogProps = {
  position: PositionView;
  onSubmit: (reason: string) => Promise<void>;
};
```

Fields: `reason` (textarea, min 3, max 240). Confirm button is
`destructive` tone.

### `GlobalKillDialog` / `GlobalResumeDialog`

Mirror the manual-exit pattern. `Resume` adds a `reset_daily_loss_circuit`
toggle.

---

## 6. Charts (Phase 10b — full spec in `06_Charts_Analytics.md`)

### `AnalyticsChart`

```ts
type AnalyticsChartProps = {
  metric: "pcr" | "oi_change" | "multi_strike_oi" | "max_pain" | "delta_pcr";
  index: "nifty50" | "banknifty";
  range: { from: string; to: string };  // ISO
  granularity: "1m" | "5m" | "15m" | "1h";
  overlays: { index: boolean; atm: boolean; maxPain: boolean };
  strikes?: number[];
  height?: number; // default 420
};
```

Wraps `lightweight-charts` with theme-aware colours from `--chart-*`.

### `PnlSparkline`

```ts
type PnlSparklineProps = { data: { ts: string; pnl: number }[]; width?: number; height?: number };
```

Tiny single-series chart for KPI tiles.

### `SnapshotCard`

```ts
type SnapshotCardProps = {
  kind: "pre_open" | "market_open" | "mid_session" | "pre_close" | "eod";
  ts: string;
  payload: SnapshotPayload;
  onOpen: () => void;
};
```

Shows a compact summary of a snapshot; click opens a full-screen sheet.

---

## 7. Overlays

### `KillSwitchSheet`

`Sheet` panel sliding in from the right. Lists segments (NSE_FO, BSE_FO,
NSE_EQ) with toggle states from `GET /capital/kill_switch`. Submit calls
`POST /capital/kill_switch` with the toggles array.

### `KeyboardShortcutsDialog`

`Dialog` listing all shortcuts in two columns. Keyed off "?" press.

### `ConfirmDialog`

```ts
type ConfirmDialogProps = {
  title: string;
  description?: ReactNode;
  confirmLabel?: string;       // default "Confirm"
  confirmTone?: "primary" | "destructive";
  onConfirm: () => Promise<void> | void;
  open: boolean;
  onOpenChange: (o: boolean) => void;
};
```

---

## 8. Feedback

### `BannerStack`, `Banner`

Defined in `02_App_Shell.md` §5–§7.

### `EmptyState`

```ts
type EmptyStateProps = {
  icon?: LucideIcon;
  title: string;
  description?: string;
  action?: { label: string; onClick: () => void };
};
```

Centered in the parent container, vertical `gap-3`. No illustrations.

### `ErrorState`

```ts
type ErrorStateProps = {
  title?: string;             // default "Something went wrong"
  description?: string;
  onRetry?: () => void;
};
```

Renders the underlying error code from API responses if available.

### `Skeleton` patterns

Define matched skeleton compositions for repeating layouts:

- `SkeletonStatTile`
- `SkeletonTableRow`
- `SkeletonCardChart`

Each one renders shadcn `Skeleton` blocks of the exact size of the live
content. **Never** use a generic `<Skeleton className="h-32 w-full" />`
inside a card.

---

## 9. Utilities

### `useFlashOnChange<T>`

```ts
function useFlashOnChange<T>(value: T, durationMs = 600): { flash: boolean }
```

Watches `value`; flips `flash` to `true` for `durationMs` after a change.

### `useWS()`

Returns the singleton WS client (`02_App_Shell.md` §3 mounts it).
Components don't call it directly — they read view data from
`useViewsStore`.

### `useViewsStore<View>`

```ts
function useViewsStore<K extends ViewKey>(key: K): ViewPayloadByKey[K] | null;
```

Selector hook. Always returns `null` if the view hasn't been delivered yet.

### `useToast()`

Re-export of sonner's `toast` typed wrapper.

### `useCommand()`

```ts
function useCommand(): { open: () => void; close: () => void; toggle: () => void };
```

Programmatic CommandMenu control.

### `cn()`

`clsx` + `tailwind-merge` re-export.

---

## 10. Composition Rules

1. **Cards never set typography sizes.** They inherit from children.
2. **Buttons use only shadcn variants** (`default`, `secondary`, `outline`,
   `ghost`, `destructive`, `link`). No new variants.
3. **Icons are siblings to text in a `flex items-center gap-2`**, never
   absolutely positioned.
4. **Forms always render labels above inputs** (no floating labels).
5. **Dialog widths**: small `sm:max-w-md`, medium `sm:max-w-lg`, large
   `sm:max-w-2xl`. Anything wider than that becomes a `Sheet`.
6. **Sheets always slide from the right**; never bottom on desktop.
7. **Tooltips are reserved** for icon-only buttons and truncated text.
   Never put long descriptions in tooltips — use `Popover`.
8. **No nested cards.** A card cannot contain another card.

---

## 11. File-Size Budget

If a component file exceeds 250 lines, split it. Common split points:

- Move `columns.ts` out of a table file.
- Move row sub-components (`<RowActions>`, `<RowDetails>`) into siblings.
- Move zod schema next to the form (`<FormName>.schema.ts`).

Linting enforces a soft warning at 250, hard error at 400.

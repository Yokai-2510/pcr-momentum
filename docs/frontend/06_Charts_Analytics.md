# Charts & Analytics (Phase 10b)

The Analytics page is the most visually rich surface. This doc captures
exactly how it composes, which library is used, and the data contracts
that feed it.

> **Library**: `lightweight-charts` v5 (TradingView). Free, dark-first,
> built for time-series with overlays, smaller than recharts, and battle-
> tested in financial UIs.

---

## 1. Page Layout (full)

```
/analytics

┌─ Page header ─────────────────────────── [Index: nifty50 ▾] [Theme: ⏾] ┐
│                                                                          │
│ ┌─ Metric tab strip ────────────────────────────────────────────────┐  │
│ │ [Open Interest] [MultiStrike OI] [Put-Call Ratio] [Max Pain]      │  │
│ │ [ΔPCR] [Premium Diff]                                              │  │
│ └────────────────────────────────────────────────────────────────────┘  │
│                                                                          │
│ ┌─ Chart panel (flex-1) ────────────────────────┐ ┌─ Customisation ─┐  │
│ │                                                │ │ Date range      │  │
│ │  AnalyticsChart                                │ │  • Today        │  │
│ │   - main series (selected metric)              │ │  • Yesterday    │  │
│ │   - dashed index price overlay (toggleable)    │ │  • Last 7 days  │  │
│ │   - vertical markers for snapshots             │ │  • Custom...    │  │
│ │   - crosshair + legend (top-left)              │ │                 │  │
│ │   - time axis (IST, hh:mm)                     │ │ Granularity     │  │
│ │                                                │ │  1m 5m 15m 1h   │  │
│ │  Height: 480 px desktop, 360 px tablet         │ │                 │  │
│ │                                                │ │ Overlays        │  │
│ └────────────────────────────────────────────────┘ │  ☑ Index price  │  │
│                                                    │  ☐ ATM marker   │  │
│ ┌─ Snapshot strip (overflow-x-auto) ─────────────────────────────┐  │  │
│ │ [pre_open] [market_open] [mid_1] [mid_2] [mid_3] [mid_4] ...   │  │  │
│ │ Each card: kind badge, ts, key metrics, "Open" CTA              │  │  │
│ └─────────────────────────────────────────────────────────────────┘  │  │
│                                                    │ Strikes (multi) │  │
│                                                    │  ☑ ATM          │  │
│                                                    │  ☑ ATM-50       │  │
│                                                    │  ☑ ATM+50       │  │
│                                                    │  ☐ ATM-100      │  │
│                                                    │  ☐ ATM+100      │  │
│                                                    │                 │  │
│                                                    │ Theme           │  │
│                                                    │  ◉ Slate Dark   │  │
│                                                    │  ◯ Carbon Dark  │  │
│                                                    │  ◯ Op. Light    │  │
│                                                    │                 │  │
│                                                    │ [Export CSV]    │  │
│                                                    └─────────────────┘  │
│                                                                          │
│ ┌─ Strategy stats row ─────────────────────────────────────────────┐   │
│ │ StatTile: Entries | Win rate | Avg PnL | Reversal rate           │   │
│ │ Heatmap: Time-of-day x weekday, value = avg PnL                  │   │
│ └────────────────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Metric Catalog

| Tab | Metric key | Series shape | Backend source |
|---|---|---|---|
| Open Interest | `oi_total` | { ts, call_oi, put_oi, total_oi } | `/analytics/option_chain/{index}` |
| MultiStrike OI | `multi_strike_oi` | { ts, strikes: { strike: oi } } | same |
| Put-Call Ratio | `pcr` | { ts, pcr } | same |
| Max Pain | `max_pain` | { ts, max_pain_strike } | same |
| ΔPCR | `delta_pcr` | { ts, interval, cumulative } | `/delta_pcr/{index}/history` |
| Premium Diff | `premium_diff` | { ts, ce_diff_atm, pe_diff_atm } | `/analytics/option_chain/{index}` (rollup field) |

The customisation rail switches the metric without remounting the chart;
`AnalyticsChart` re-uses the same `IChartApi` instance and swaps series.

---

## 3. AnalyticsChart Component

### 3.1 Files

```
components/charts/
├── AnalyticsChart.tsx               # public component
├── chartTheme.ts                    # maps tokens → IChartApi options
├── seriesBuilders/
│   ├── pcr.ts
│   ├── oiTotal.ts
│   ├── multiStrikeOi.ts
│   ├── maxPain.ts
│   ├── deltaPcr.ts
│   └── premiumDiff.ts
└── overlays/
    ├── indexPriceOverlay.ts
    └── snapshotMarkers.ts
```

### 3.2 Public API

```ts
type AnalyticsChartProps = {
  metric: MetricKey;
  index: "nifty50" | "banknifty";
  range: { from: string; to: string };       // ISO IST
  granularity: "1m" | "5m" | "15m" | "1h";
  overlays: {
    index: boolean;
    atm: boolean;
    snapshots: boolean;
  };
  strikes?: number[];                         // multi-strike OI only
  height?: number;                             // default 480
  onCrosshairMove?: (info: CrosshairInfo) => void;
};
```

Internally:

1. Mounts `lightweight-charts` once with options derived from `chartTheme.ts`.
2. Subscribes to `themeStore.effectiveTheme`; on change, calls
   `chart.applyOptions()` with the new theme tokens.
3. Loads data via the relevant `analyticsApi.*` call. Caches by
   `(metric, index, range, granularity)` key inside a small in-memory map
   so tab switching doesn't re-fetch.
4. Updates the active series in place when `metric` changes.
5. Renders the index-price overlay when `overlays.index === true`.
6. Renders snapshot markers (vertical dashed lines + chips at top) when
   `overlays.snapshots === true`.

### 3.3 Theme binding

`chartTheme.ts` exports a function `getChartOptions(themeName)`:

```ts
function getChartOptions(theme: ThemeName): DeepPartial<ChartOptions> {
  const cssVar = (name: string) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return {
    layout: {
      background: { type: "solid", color: `hsl(${cssVar("--card")})` },
      textColor:  `hsl(${cssVar("--muted-foreground")})`,
      fontFamily: 'var(--font-sans), system-ui, sans-serif',
    },
    grid: {
      vertLines: { color: `hsl(${cssVar("--chart-grid")})` },
      horzLines: { color: `hsl(${cssVar("--chart-grid")})` },
    },
    crosshair: { mode: 1 },                    // magnet
    rightPriceScale: { borderColor: `hsl(${cssVar("--chart-axis")})` },
    timeScale: {
      borderColor: `hsl(${cssVar("--chart-axis")})`,
      timeVisible: true,
      secondsVisible: false,
    },
  };
}
```

Series colours pull from `--chart-1..5`; lines are 2 px thick by default,
overlay (index price) is 1 px **dashed**.

---

## 4. Snapshot Strip

### 4.1 Snapshot kinds

Each entry has a `kind` enum, `ts`, and a `payload` object:

| `kind` | When captured | Payload (suggested) |
|---|---|---|
| `pre_open` | 09:14:00 IST | `{ atm, strike_basket, ce_premiums, pe_premiums }` |
| `market_open` | 09:15:30 IST | `{ atm, ce_premiums, pe_premiums, total_oi, pcr }` |
| `mid_session_1` | 10:30 | full option chain rollup |
| `mid_session_2` | 12:00 | full option chain rollup |
| `mid_session_3` | 13:30 | full option chain rollup |
| `mid_session_4` | 14:30 | full option chain rollup |
| `pre_close` | 15:25 | rollup + last position state |
| `eod` | 15:35 | EOD wrap with realized PnL, trade count |

### 4.2 SnapshotCard

```ts
type SnapshotCardProps = {
  kind: SnapshotKind;
  ts: string;
  payload: SnapshotPayload;
  onOpen: () => void;
};
```

Card content:
- Header: `kind` formatted ("Pre-open"), `ts` formatted IST time.
- Two-column grid of 4–6 key metrics depending on kind.
- Footer: small "Open detailed view" button → opens a `Sheet` with full
  payload (`<JsonPreview>` plus a strike-by-strike OI table).

Strip behaves like a horizontal scroller; current "now" position is
highlighted with `border-l-4 border-primary`.

### 4.3 Markers on chart

When `overlays.snapshots === true`, the chart paints vertical dashed lines
at each snapshot `ts`. A small chip at the top of the chart shows the
`kind` label. Hovering the chip dims the rest and pops a tooltip with the
3 most relevant payload metrics.

---

## 5. Strategy Stats Row

Row of `StatTile`s plus one heatmap chart:

| Tile | Source |
|---|---|
| Entries | `analyticsApi.strategy.summary.entries` |
| Win rate | `analyticsApi.strategy.summary.win_rate` |
| Avg PnL | `analyticsApi.strategy.summary.avg_pnl` |
| Reversal rate | `analyticsApi.strategy.summary.reversal_rate` |

Heatmap (`StrategyHeatmap` component, custom):
- X-axis: 15-min buckets across market hours (9:15 – 15:30).
- Y-axis: weekday (Mon..Fri).
- Cell colour: avg PnL — `var(--success)` for positive, `var(--destructive)` for negative, intensity scaled.
- Implementation: SVG grid; no external library.
- Tooltip on cell: `count`, `avg_pnl`, `win_rate` for that bucket.

---

## 6. Customisation Rail

Sticky right-side `Card` (`w-72`). Sections in order:

1. **Date range** — `RadioGroup` with quick picks + "Custom..." which opens
   a `Calendar` popover with `from / to`.
2. **Granularity** — `ToggleGroup` of `1m | 5m | 15m | 1h`.
3. **Overlays** — three `Switch`es with labels: `Index price`, `ATM marker`,
   `Snapshots`.
4. **Strikes** — only for `multi_strike_oi`. Multi-select `Checkbox` list of
   ATM and ±2 strike steps; defaults to ATM, ATM±1.
5. **Theme** — `RadioGroup` mirroring the global theme menu.
6. **Export** — `Button` "Export CSV" (downloads the currently visible
   series).

All controls write to `useAnalyticsStore` (defined in `05_State_and_Data.md`
extension below) so the URL can serialize the state.

### 6.1 URL state

`/analytics?metric=pcr&index=nifty50&range=today&granularity=5m&overlays=index,snapshots`

Implemented with a tiny `useUrlState()` helper that round-trips the
analytics store to the URL.

---

## 7. Backend Contract (10b)

These endpoints must exist; details are in `docs/API.md` Phase 10b section.

### 7.1 `GET /analytics/option_chain/{index}`

Query: `from` (ISO date), `to` (ISO date), `granularity` (`1m|5m|15m|1h`),
`metrics` (CSV: `pcr,oi_change,multi_strike_oi,max_pain,oi_total,premium_diff`),
`strikes` (CSV of integers, optional, for multi-strike).

Response:

```json
{
  "index": "nifty50",
  "granularity": "5m",
  "series": [
    {
      "ts": "2026-04-28T09:15:00+05:30",
      "atm": 24500,
      "call_oi": 12450000,
      "put_oi": 14580000,
      "pcr": 1.17,
      "max_pain": 24500,
      "premium_diff": { "ce_atm": 1.5, "pe_atm": -0.8 },
      "strike_oi": { "24450": 1420000, "24500": 1750000, "24550": 1300000 }
    }
  ]
}
```

### 7.2 `GET /analytics/snapshots/{index}`

Query: `date` (ISO date, required), `kind` (optional filter).

Response:

```json
{
  "index": "nifty50",
  "items": [
    { "ts": "2026-04-28T09:14:00+05:30", "kind": "pre_open", "payload": { ... } },
    { "ts": "2026-04-28T09:15:30+05:30", "kind": "market_open", "payload": { ... } }
  ]
}
```

### 7.3 `GET /analytics/strategy/{index}`

Query: `from`, `to`.

Response:

```json
{
  "index": "nifty50",
  "summary": {
    "entries": 42,
    "win_rate": 0.62,
    "avg_pnl": 1845.0,
    "reversal_rate": 0.21
  },
  "heatmap": [
    { "weekday": 1, "bucket_hhmm": "09:15", "count": 7, "avg_pnl": 1450.0, "win_rate": 0.71 },
    { "weekday": 1, "bucket_hhmm": "09:30", "count": 9, "avg_pnl": 980.0,  "win_rate": 0.55 }
  ]
}
```

---

## 8. Performance

- Maximum points per series: 2,000. The granularity selector caps queries
  so the result fits.
- Chart render budget: < 16 ms median frame on a single tick update.
- Tab switch budget: < 300 ms median (data cached after first fetch within
  session).
- Zoom / pan stays at 60 fps on a 2-year-old laptop.

---

## 9. Acceptance

- All six metric tabs render without console errors against canned data.
- Theme toggle updates the chart background, grid, and axis colours
  without remount.
- Custom date range survives a refresh (URL state).
- Export CSV downloads a file matching the visible series.
- Snapshot markers appear/disappear with the toggle.
- Heatmap cells respond to hover with a tooltip.
- All three themes look intentional, not just "tinted".

---

## 10. Open Questions (to settle before 10b implementation)

- Do we persist 1-second OI rollups or only 1-minute? **Default**: 1-minute,
  with on-demand 1m → 5m / 15m aggregation in the API.
- Does `multi_strike_oi` need to support arbitrary strikes or only ATM ±N?
  **Default**: ATM ±2 (5 strikes).
- Time zone display: always IST? **Yes**, per `lib/utils/ist.ts`.
- Heatmap colour scale: linear or log? **Default**: symmetric linear with
  clipping at the 95th percentile.

These are documented for the operator to confirm before backend work
starts.

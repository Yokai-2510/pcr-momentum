# Design System

This document defines every visual token in the app. Components consume
tokens; tokens are defined once per theme. **Never hard-code colors,
spacing, radii, or font sizes inside a component.**

---

## 1. Principles

1. **Tokens before components.** Every visual decision lives in a CSS
   variable that any theme can override.
2. **Three themes, one component tree.** The app supports three themes by
   swapping a single `data-theme` attribute on `<html>`. No component
   re-renders or conditional class chains based on theme.
3. **Density is content-driven, not theme-driven.** Tables and dashboards
   set their own row density; themes do not.
4. **Numbers always use mono.** Anything that compares vertically (PnL,
   strikes, premiums, OI counts) uses JetBrains Mono. Body uses Inter.
5. **Motion is restrained.** ≤ 200 ms transitions; no spring/bounce; no
   parallax; no animated gradients.
6. **No glassmorphism, no gradients, no neon glows.** Flat, calm, dense.

---

## 2. Themes

The app ships **three** themes. Operator picks via Command Palette (⌘K →
"Switch theme") or system menu. Stored in `localStorage` under `pcr.theme`.

| Slug | Name | Use case |
|---|---|---|
| `slate-dark` | **Slate Dark** (default) | All-day trading, low eye fatigue |
| `carbon-dark` | **Carbon Dark** | Pure-black OLED-friendly, TradingView-like |
| `operator-light` | **Operator Light** | Daytime / bright office / printed reports |

The token names are identical across themes. Only values differ.

### 2.1 Slate Dark (default)

```css
:root,
[data-theme="slate-dark"] {
  /* Surface */
  --background:           222 22% 6%;     /* page bg */
  --foreground:           210 18% 92%;    /* primary text */
  --card:                 222 20% 9%;     /* card bg */
  --card-foreground:      210 18% 92%;
  --popover:              222 24% 11%;
  --popover-foreground:   210 18% 92%;
  --muted:                222 14% 14%;
  --muted-foreground:     220 10% 60%;
  --border:               222 14% 18%;
  --input:                222 14% 16%;
  --ring:                 217 91% 60%;    /* focus ring = primary */

  /* Brand / interactive */
  --primary:              217 91% 60%;    /* blue-500 */
  --primary-foreground:   0   0%  100%;
  --secondary:            222 14% 16%;
  --secondary-foreground: 210 18% 92%;
  --accent:               222 14% 16%;
  --accent-foreground:    210 18% 92%;

  /* Semantic */
  --success:              152 64% 44%;    /* PnL up */
  --success-foreground:   0   0%  100%;
  --destructive:          0   72% 51%;    /* PnL down, kill, errors */
  --destructive-foreground: 0 0% 100%;
  --warning:              38  92% 50%;    /* degraded mode */
  --warning-foreground:   30  30% 10%;
  --info:                 217 91% 60%;
  --info-foreground:      0   0%  100%;

  /* Charts (data palette, theme-aware) */
  --chart-1:              217 91% 60%;    /* primary line */
  --chart-2:              152 64% 44%;    /* success / call OI */
  --chart-3:              0   72% 51%;    /* destructive / put OI */
  --chart-4:              38  92% 50%;    /* warning / max pain */
  --chart-5:              268 71% 65%;    /* aux purple */
  --chart-grid:           222 14% 18%;
  --chart-axis:           220 10% 50%;
  --chart-overlay-index:  220 10% 80%;    /* dashed price overlay */

  /* Radius (single token) */
  --radius:               0.5rem;         /* 8px */
}
```

### 2.2 Carbon Dark

```css
[data-theme="carbon-dark"] {
  --background:           0   0%  3%;     /* near-black */
  --foreground:           0   0%  92%;
  --card:                 0   0%  6%;
  --card-foreground:      0   0%  92%;
  --popover:              0   0%  8%;
  --popover-foreground:   0   0%  92%;
  --muted:                0   0%  10%;
  --muted-foreground:     0   0%  60%;
  --border:               0   0%  14%;
  --input:                0   0%  12%;
  --ring:                 38  92% 55%;    /* amber accent */

  --primary:              38  92% 55%;    /* amber */
  --primary-foreground:   0   0%  6%;
  --secondary:            0   0%  12%;
  --secondary-foreground: 0   0%  92%;
  --accent:               0   0%  12%;
  --accent-foreground:    0   0%  92%;

  --success:              152 64% 50%;
  --success-foreground:   0   0%  6%;
  --destructive:          0   72% 55%;
  --destructive-foreground: 0 0% 100%;
  --warning:              38  92% 55%;
  --warning-foreground:   0   0%  6%;
  --info:                 200 90% 60%;
  --info-foreground:      0   0%  6%;

  --chart-1:              38  92% 55%;
  --chart-2:              152 64% 50%;
  --chart-3:              0   72% 55%;
  --chart-4:              200 90% 60%;
  --chart-5:              268 71% 65%;
  --chart-grid:           0   0%  14%;
  --chart-axis:           0   0%  50%;
  --chart-overlay-index:  0   0%  80%;

  --radius:               0.5rem;
}
```

### 2.3 Operator Light

```css
[data-theme="operator-light"] {
  --background:           220 20% 98%;
  --foreground:           222 30% 12%;
  --card:                 0   0%  100%;
  --card-foreground:      222 30% 12%;
  --popover:              0   0%  100%;
  --popover-foreground:   222 30% 12%;
  --muted:                220 16% 94%;
  --muted-foreground:     222 12% 40%;
  --border:               220 14% 88%;
  --input:                220 14% 92%;
  --ring:                 217 91% 50%;

  --primary:              217 91% 50%;
  --primary-foreground:   0   0%  100%;
  --secondary:            220 16% 94%;
  --secondary-foreground: 222 30% 12%;
  --accent:               220 16% 94%;
  --accent-foreground:    222 30% 12%;

  --success:              152 64% 36%;
  --success-foreground:   0   0%  100%;
  --destructive:          0   72% 45%;
  --destructive-foreground: 0 0% 100%;
  --warning:              38  92% 45%;
  --warning-foreground:   0   0%  100%;
  --info:                 217 91% 50%;
  --info-foreground:      0   0%  100%;

  --chart-1:              217 91% 50%;
  --chart-2:              152 64% 36%;
  --chart-3:              0   72% 45%;
  --chart-4:              38  92% 45%;
  --chart-5:              268 60% 50%;
  --chart-grid:           220 14% 88%;
  --chart-axis:           222 12% 50%;
  --chart-overlay-index:  222 12% 30%;

  --radius:               0.5rem;
}
```

---

## 3. Tailwind Config

`tailwind.config.ts` exposes every token as a Tailwind color so you can
write `bg-card`, `text-foreground`, `border-border`, etc. **Do not** use
raw `bg-zinc-900` style classes anywhere outside this file.

```ts
import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: ["class", '[data-theme="slate-dark"]', '[data-theme="carbon-dark"]'],
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    container: {
      center: true,
      padding: "1.5rem",
      screens: { "2xl": "1440px" },
    },
    extend: {
      colors: {
        background:   "hsl(var(--background))",
        foreground:   "hsl(var(--foreground))",
        card:         { DEFAULT: "hsl(var(--card))", foreground: "hsl(var(--card-foreground))" },
        popover:      { DEFAULT: "hsl(var(--popover))", foreground: "hsl(var(--popover-foreground))" },
        muted:        { DEFAULT: "hsl(var(--muted))", foreground: "hsl(var(--muted-foreground))" },
        border:       "hsl(var(--border))",
        input:        "hsl(var(--input))",
        ring:         "hsl(var(--ring))",
        primary:      { DEFAULT: "hsl(var(--primary))", foreground: "hsl(var(--primary-foreground))" },
        secondary:    { DEFAULT: "hsl(var(--secondary))", foreground: "hsl(var(--secondary-foreground))" },
        accent:       { DEFAULT: "hsl(var(--accent))", foreground: "hsl(var(--accent-foreground))" },
        success:      { DEFAULT: "hsl(var(--success))", foreground: "hsl(var(--success-foreground))" },
        destructive:  { DEFAULT: "hsl(var(--destructive))", foreground: "hsl(var(--destructive-foreground))" },
        warning:      { DEFAULT: "hsl(var(--warning))", foreground: "hsl(var(--warning-foreground))" },
        info:         { DEFAULT: "hsl(var(--info))", foreground: "hsl(var(--info-foreground))" },
        chart: {
          1: "hsl(var(--chart-1))",
          2: "hsl(var(--chart-2))",
          3: "hsl(var(--chart-3))",
          4: "hsl(var(--chart-4))",
          5: "hsl(var(--chart-5))",
          grid: "hsl(var(--chart-grid))",
          axis: "hsl(var(--chart-axis))",
          overlay: "hsl(var(--chart-overlay-index))",
        },
      },
      borderRadius: {
        lg: "var(--radius)",
        md: "calc(var(--radius) - 2px)",
        sm: "calc(var(--radius) - 4px)",
      },
      fontFamily: {
        sans: ["var(--font-sans)", "system-ui", "sans-serif"],
        mono: ["var(--font-mono)", "ui-monospace", "monospace"],
      },
      fontSize: {
        "ui-2xs": ["0.6875rem", { lineHeight: "1rem" }],   /* 11px */
        "ui-xs":  ["0.75rem",   { lineHeight: "1.125rem" }], /* 12px */
        "ui-sm":  ["0.8125rem", { lineHeight: "1.25rem" }], /* 13px */
        "ui-md":  ["0.875rem",  { lineHeight: "1.375rem" }], /* 14px — body default */
        "ui-lg":  ["1rem",      { lineHeight: "1.5rem" }],   /* 16px */
        "h-sm":   ["1.125rem",  { lineHeight: "1.5rem", letterSpacing: "-0.01em" }],
        "h-md":   ["1.25rem",   { lineHeight: "1.625rem", letterSpacing: "-0.015em" }],
        "h-lg":   ["1.5rem",    { lineHeight: "1.875rem", letterSpacing: "-0.02em" }],
        "h-xl":   ["1.875rem",  { lineHeight: "2.25rem",  letterSpacing: "-0.025em" }],
        "kpi":    ["2rem",      { lineHeight: "2.25rem",  letterSpacing: "-0.03em", fontWeight: "600" }],
        "kpi-lg": ["2.5rem",    { lineHeight: "2.75rem",  letterSpacing: "-0.035em", fontWeight: "600" }],
      },
      boxShadow: {
        card:    "0 1px 0 0 hsl(var(--border)) inset",
        popover: "0 8px 24px -8px hsl(0 0% 0% / 0.45)",
      },
      transitionDuration: { DEFAULT: "150ms" },
      keyframes: {
        "fade-in":  { from: { opacity: "0" }, to: { opacity: "1" } },
        "slide-in-from-bottom": {
          from: { transform: "translateY(8px)", opacity: "0" },
          to:   { transform: "translateY(0)",  opacity: "1" },
        },
        pulse: { "50%": { opacity: "0.4" } },
      },
      animation: {
        "fade-in":  "fade-in 150ms ease-out",
        "slide-in": "slide-in-from-bottom 200ms cubic-bezier(0.2, 0.8, 0.2, 1)",
        "pulse-soft": "pulse 1.6s ease-in-out infinite",
      },
    },
  },
  plugins: [require("tailwindcss-animate")],
};
export default config;
```

---

## 4. Typography

| Family | Weights | Use |
|---|---|---|
| **Inter** (variable) | 400, 500, 600, 700 | UI, body, headings, navigation |
| **JetBrains Mono** (variable) | 400, 500, 600 | All numbers, codes, IDs, timestamps in dense tables |

Self-host both via `next/font/local` (in `app/layout.tsx`). Bind to CSS
variables `--font-sans` and `--font-mono`.

### 4.1 Type scale (semantic, theme-agnostic)

| Class | Size | Usage |
|---|---|---|
| `text-ui-2xs` | 11/16 | Table footers, micro-labels, breadcrumb |
| `text-ui-xs`  | 12/18 | Secondary labels, axis ticks, status pills |
| `text-ui-sm`  | 13/20 | Compact tables, secondary copy |
| `text-ui-md`  | 14/22 | **Body default** |
| `text-ui-lg`  | 16/24 | Sidebar nav text on hover, mid-emphasis |
| `text-h-sm`   | 18/24 | Card titles, section labels |
| `text-h-md`   | 20/26 | Sub-page headers |
| `text-h-lg`   | 24/30 | Page headers |
| `text-h-xl`   | 30/36 | Onboarding hero only |
| `text-kpi`    | 32/36 | Dashboard tile values |
| `text-kpi-lg` | 40/44 | "Total PnL today" hero on dashboard |

### 4.2 Number formatting

Always render numbers via `formatINR()`, `formatPct()`, `formatLots()` from
`lib/utils/format.ts`:

- **Currency**: `₹2,42,156.50` (Indian grouping). Negatives: `-₹2,42,156.50`
  in `text-destructive`. Positives prefixed with `+` only in deltas, never
  on absolute values.
- **Percent**: `+0.71%` / `-0.32%` with 2 decimals.
- **Premium**: 2 decimals, no currency prefix (`12.50`).
- **Strike**: integer with thousands separator (`24,500`).
- **OI**: human-readable abbreviations only ≥ 1 lakh (`12.4L`, `1.2Cr`).
  Below that, render integer with grouping.

---

## 5. Spacing

Tailwind scale only. Do not invent values. Common patterns:

| Token | Px | Use |
|---|---|---|
| `gap-1` | 4 | Inline icon + label |
| `gap-2` | 8 | Tight badges in a row |
| `gap-3` | 12 | Form field rows |
| `gap-4` | 16 | Card body sections |
| `gap-6` | 24 | Top-level page sections |
| `gap-8` | 32 | Between hero and grid |
| `p-3`   | 12 | Compact card padding |
| `p-4`   | 16 | Default card padding |
| `p-6`   | 24 | Large card padding |
| `px-6 py-4` | — | Page header strip |
| `space-y-6` | 24 | Default vertical rhythm between page sections |

---

## 6. Radius

One token: `--radius: 8px`. Tailwind exposes:

- `rounded-sm` → 4px (badges, pills)
- `rounded-md` → 6px (inputs, buttons)
- `rounded-lg` → 8px (cards, dialogs)

Nothing else. No `rounded-xl`, no `rounded-3xl`, no `rounded-full` except
on avatars and dot indicators.

---

## 7. Elevation

Two levels only:

- **Card**: `bg-card border border-border shadow-card` (the inset 1px line at
  the top is what gives serious dashboards their crispness).
- **Popover / Dialog**: `bg-popover border border-border shadow-popover`.

No floating shadows, no hover-rise, no neon outline.

---

## 8. Iconography

- Library: `lucide-react`.
- Default size in nav and buttons: `h-4 w-4` (16px).
- In KPI tiles: `h-5 w-5` (20px).
- Stroke width: default (1.5). Do not change.
- Icon colour follows `currentColor`. Compose via `text-foreground`,
  `text-muted-foreground`, `text-success`, `text-destructive`.

---

## 9. Status / Semantic Colour Mapping

| Domain term | Token |
|---|---|
| Trading active | `success` |
| Trading halted, manual kill | `destructive` |
| Awaiting credentials, auth invalid, degraded health | `warning` |
| Mode = paper | `info` |
| Mode = live | `success` |
| Position OPEN | `info` |
| Position EXIT_PENDING | `warning` |
| Position CLOSED_TGT | `success` |
| Position CLOSED_SL, CLOSED_REVERSAL | `destructive` |
| Position CLOSED_TIME, CLOSED_MANUAL | `muted-foreground` |

These mappings are codified in `lib/utils/semanticColor.ts`. Components
import the helpers; they do not branch on enum strings inline.

---

## 10. Motion

- **Duration**: 150 ms default for hover, 200 ms for enter, 100 ms for exit.
- **Easing**: `cubic-bezier(0.2, 0.8, 0.2, 1)` for enter; `ease-out` for hover.
- **Use cases**:
  - Dialog enter/exit (Radix defaults are fine).
  - Tooltip fade.
  - Sidebar collapse: 200 ms width transition.
  - **Numbers**: never animate. Live ticks update instantly. The only
    visual feedback for a value change is a 600 ms flash (`bg-success/15`
    fade-out) on the cell. Hook: `useFlashOnChange(value)`.
- **Forbidden**: spring/bounce, parallax, animated SVG strokes, route
  page-transition animations.

---

## 11. Accessibility

- Minimum contrast ratio: 4.5:1 for body, 3:1 for large text. Verified for
  all three themes against WCAG AA.
- Focus ring: always `ring-2 ring-ring ring-offset-2 ring-offset-background`.
  Never remove the ring; never reduce its width.
- Every dialog must have a `<DialogTitle>` (visually hidden if needed).
- Every icon-only button must have `aria-label`.
- Number cells in tables get `aria-label` of "Profit-and-loss: minus two
  thousand" etc., generated by `formatA11y(value)`.
- Keyboard shortcuts:
  - `⌘K` / `Ctrl+K`: Command palette
  - `⌘B` / `Ctrl+B`: Toggle sidebar
  - `g d`: Go to dashboard
  - `g p`: Go to positions
  - `g a`: Go to analytics
  - `g c`: Go to configs
  - `g o`: Go to operations
  - `?`: Show keyboard shortcuts help

---

## 12. Density

The app supports two table densities, set per page (not per theme):

- **Default**: row height 44 px, padding `py-2.5 px-4`.
- **Compact**: row height 32 px, padding `py-1.5 px-3`, font `text-ui-sm`.

`/positions` and `/analytics` default to **compact**. `/configs` and
`/reports/[id]` default to **default**.

---

## 13. Empty / Loading / Error States

Every data-driven view defines all four states:

- **Loading**: shadcn `Skeleton` matching the eventual layout. Never a
  spinner inside a card.
- **Empty**: `EmptyState` component with icon, title, optional CTA. No
  illustrations, no marketing copy.
- **Error**: `ErrorState` with retry button. Caller decides whether to
  retry the failed query or reload.
- **Disconnected**: `BannerStack` shows a top banner; the underlying view
  keeps the last good data with a `text-muted-foreground` overlay.

Wire all four states through every page. **Do not** ship a page without
them.

---

## 14. Visual Reference Bar

When in doubt, match the *flatness* of these references:

- Linear (linear.app) — sidebar, command palette, density.
- Vercel dashboard — card composition, KPI tiles.
- TradingView Lite — chart overlays, time-axis treatment.
- Stripe Dashboard — status pills, dialog patterns.

Do **not** match: Bloomberg Terminal density (too dense), broker apps
(too noisy), generic admin templates (too spaced out).

# App Shell

The shell is the persistent UI chrome around every operator page: side
navigation, top bar, banners, command palette, toaster. Build it once, get
it right, and never touch it again.

---

## 1. Visual Anatomy

```
┌─────────────────────────────────────────────────────────────────────────┐
│ TopBar (h-14)                                                            │
│ [logo] [env-badge] [mode-pill]      [global-status] [⌘K] [theme] [user] │
├──────────┬──────────────────────────────────────────────────────────────┤
│ SideNav  │ BannerStack (sticky, 0..N banners)                           │
│ (w-60 →  │                                                              │
│  w-14)   │ Page header                                                  │
│          │ ────────────                                                 │
│  Items:  │                                                              │
│  Dash    │ Page content                                                 │
│  Posit.  │                                                              │
│  Analy.  │                                                              │
│  Conf.   │                                                              │
│  Oper.   │                                                              │
│          │                                                              │
│  Footer: │                                                              │
│  systmd  │                                                              │
└──────────┴──────────────────────────────────────────────────────────────┘
                                                          [Toaster bottom-right]
```

- Side nav: 240 px wide; collapses to 56 px (icon-only) via `⌘B` or chevron.
- Top bar: 56 px tall, sticky, `border-b border-border`.
- Banners: stacked under top bar, dismissible, max 3 visible (overflow → toast).
- Toaster: bottom-right, max 4 stacked.

---

## 2. Files

```
components/shell/
├── AppShell.tsx              # the (operator) layout wrapper
├── SideNav.tsx               # left rail with NavItem children
├── NavItem.tsx
├── TopBar.tsx
├── EnvBadge.tsx              # "DEV" / "STAGING" / "PROD"
├── ModePill.tsx              # paper | live
├── GlobalStatus.tsx          # connection dot + last-update timestamp
├── ThemeMenu.tsx
├── UserMenu.tsx
├── CommandMenu.tsx           # ⌘K
├── BannerStack.tsx
└── Banner.tsx                # individual banner (info | warning | destructive)
```

---

## 3. AppShell

### 3.1 Responsibilities

- Mounts `WSConnection` (singleton).
- Mounts `Toaster`.
- Mounts `CommandMenu`.
- Reads `authStore` and redirects to `/login` if no JWT or expired.
- Renders `SideNav`, `TopBar`, `BannerStack`, `<main>`.

### 3.2 Pseudocode

```tsx
// app/(operator)/layout.tsx
import { redirect } from "next/navigation";
import { AppShell } from "@/components/shell/AppShell";
import { authBootstrap } from "@/lib/auth/bootstrap";

export default async function OperatorLayout({ children }: { children: React.ReactNode }) {
  const auth = await authBootstrap(); // server-side: reads cookie, validates
  if (!auth.ok) redirect("/login");
  return <AppShell>{children}</AppShell>;
}
```

```tsx
// components/shell/AppShell.tsx
"use client";
import { useEffect } from "react";
import { Toaster } from "@/components/ui/sonner";
import { SideNav } from "./SideNav";
import { TopBar } from "./TopBar";
import { BannerStack } from "./BannerStack";
import { CommandMenu } from "./CommandMenu";
import { useWSConnection } from "@/lib/ws/useWSConnection";
import { useUiStore } from "@/lib/stores/uiStore";

export function AppShell({ children }: { children: React.ReactNode }) {
  const sidebarCollapsed = useUiStore((s) => s.sidebarCollapsed);
  useWSConnection(); // mounts the singleton WS

  return (
    <div className="flex min-h-screen bg-background">
      <SideNav collapsed={sidebarCollapsed} />
      <div className="flex flex-1 flex-col">
        <TopBar />
        <BannerStack />
        <main className="flex-1 overflow-auto px-6 py-6">
          <div className="mx-auto w-full max-w-[1440px]">{children}</div>
        </main>
      </div>
      <CommandMenu />
      <Toaster />
    </div>
  );
}
```

---

## 4. SideNav

### 4.1 Items

| Order | Icon (lucide) | Label | Path | Shortcut |
|---|---|---|---|---|
| 1 | `LayoutDashboard` | Dashboard | `/dashboard` | `g d` |
| 2 | `Briefcase` | Positions | `/positions` | `g p` |
| 3 | `LineChart` | Analytics | `/analytics` | `g a` |
| 4 | `SlidersHorizontal` | Configs | `/configs` | `g c` |
| 5 | `Power` | Operations | `/operations` | `g o` |

Footer (always at the bottom, sticky):
- Engine-status mini grid (5 dots: init, data_pipeline, strategy, order_exec,
  api_gateway). Each dot is `bg-success` when alive, `bg-destructive` when
  not, with a tooltip showing `last_hb_ts`.
- Build version (small, `text-ui-2xs text-muted-foreground`).

### 4.2 Behaviour

- Active item: `bg-accent text-accent-foreground`.
- Inactive: `text-muted-foreground hover:bg-accent/60 hover:text-foreground`.
- Collapsed: only icons visible; tooltip shows label on hover.
- Width transition: `transition-[width] duration-200`.

### 4.3 NavItem props

```ts
type NavItemProps = {
  icon: LucideIcon;
  label: string;
  href: string;
  shortcut?: string;
  badge?: { count: number; tone: "info" | "warning" | "destructive" };
};
```

`badge` is for things like "3 open positions" on Positions, or "1" on
Operations when a kill switch is active.

---

## 5. TopBar

### 5.1 Left cluster

- **Logo**: text wordmark `pcr-momentum` in `font-mono`. Tap → `/dashboard`.
- **EnvBadge**: from `process.env.NEXT_PUBLIC_APP_ENV`. Tones:
  - `dev` → `bg-info/15 text-info`
  - `staging` → `bg-warning/15 text-warning`
  - `prod` → hidden (badge invisible to reduce noise)
- **ModePill**: reads `views.dashboard.system_state.mode`.
  - `paper` → `bg-info/15 text-info`, label "PAPER"
  - `live` → `bg-success/15 text-success`, label "LIVE"

### 5.2 Right cluster

- **GlobalStatus**: shows WS connection + last view update timestamp.
  - Connected & fresh (< 3 s old): green dot, "Live · 2s ago".
  - Stale (3–10 s): amber dot, "Live · 8s ago".
  - Disconnected: red dot, "Reconnecting…".
- **Search / Command** trigger: `<Button variant="outline">⌘K · Search</Button>`.
- **ThemeMenu**: `<DropdownMenu>` with three theme items + "Auto" (system
  pref). Persists choice to `localStorage`.
- **UserMenu**: shows `{username}`, items: "Sign out", "Keyboard shortcuts",
  "Account settings".

### 5.3 Banners

`BannerStack` lives directly under the top bar; banners come from
`useBanners()` hook which reads from these sources:

| Trigger | Tone | Message |
|---|---|---|
| `dashboard.system_state.trading_active === false` and reason `awaiting_credentials` | warning | "Awaiting Upstox credentials — [Configure]" |
| reason `auth_invalid` | warning | "Upstox auth invalid — [Renew token]" |
| reason `manual_kill` | destructive | "Trading halted manually. [Resume]" |
| reason `daily_loss_circuit` | destructive | "Daily loss circuit triggered. [Review]" |
| WS disconnected ≥ 5 s | warning | "Disconnected — reconnecting" |
| Health summary `DOWN` | destructive | "System health: DOWN. [Diagnose]" |

Each banner has an action button on the right. Buttons map to:
- "Configure" → `/onboarding/credentials`
- "Renew token" → triggers `POST /commands/upstox_token_request` then opens
  a toast with the WhatsApp URL.
- "Resume" → opens `GlobalResumeDialog`.
- "Review" → `/operations` with `?focus=daily_loss`.
- "Diagnose" → `/operations` with `?focus=health`.

---

## 6. CommandMenu (⌘K)

Built on shadcn `Command` (cmdk). Opens on `⌘K` / `Ctrl+K`. Closes on
`Escape` or clicking outside.

### 6.1 Sections

1. **Pages**
   - Go to Dashboard / Positions / Analytics / Configs / Operations.
2. **Commands**
   - Halt nifty50, Halt banknifty, Resume nifty50, Resume banknifty.
   - Global kill, Global resume.
   - Refresh instruments.
   - Request Upstox token.
   - Manual exit (opens position picker sub-menu).
3. **Theme**
   - Switch to Slate Dark / Carbon Dark / Operator Light / Auto.
4. **Density**
   - Toggle compact tables.
5. **Account**
   - Sign out, Keyboard shortcuts.

Filter input matches across all items via `cmdk` fuzzy match.

### 6.2 Action contract

Each command item has:

```ts
type CommandAction = {
  id: string;
  group: "pages" | "commands" | "theme" | "density" | "account";
  label: string;
  shortcut?: string;
  icon?: LucideIcon;
  perform: () => void | Promise<void>;
};
```

`perform` either calls a route push or invokes a typed REST helper from
`lib/api/endpoints.ts`. Confirmation dialogs (e.g. global kill) intercept
inside `perform` before any API call.

---

## 7. Banner Component

```tsx
type BannerProps = {
  id: string;
  tone: "info" | "warning" | "destructive";
  title: string;
  description?: string;
  action?: { label: string; onClick: () => void };
  dismissible?: boolean;
};
```

Render rules:
- Icon: `Info` for info, `AlertTriangle` for warning, `Octagon` for destructive.
- Background: `bg-{tone}/10 border-{tone}/30`.
- Text: `text-{tone}` for title, `text-foreground` for description.
- Action button: `variant="outline" size="sm"`, with the tone applied to the
  border (`border-{tone}/40 text-{tone} hover:bg-{tone}/10`).

---

## 8. Toaster

Use shadcn `Sonner` wrapper. Default tone tokens:

- `toast.success(...)` for confirmation of operator actions.
- `toast.error(...)` for failed REST calls (after the typed error envelope
  unpacks `code` + `message`).
- `toast.info(...)` for WS notifications with `level: "INFO"`.
- `toast.warning(...)` for WS notifications with `level: "WARNING"`.

Position: `bottom-right`. Duration: 5 s default, 8 s for warnings, sticky
for `CRITICAL`.

---

## 9. Providers (`app/layout.tsx`)

Top-level providers, in order:

1. `<ThemeProvider>` — sets `data-theme` from `localStorage` on first paint
   to avoid FOUC.
2. `<NextThemesProvider>` (only if using `next-themes`; otherwise hand-rolled).
3. `<TooltipProvider delayDuration={200}>`.
4. `<SonnerToaster richColors expand={false}>` (mounted inside AppShell).
5. App content.

Fonts (`next/font/local`):

```ts
const fontSans = localFont({
  src: "../public/fonts/InterVariable.woff2",
  variable: "--font-sans",
  display: "swap",
});
const fontMono = localFont({
  src: "../public/fonts/JetBrainsMono-Variable.woff2",
  variable: "--font-mono",
  display: "swap",
});
```

Apply on `<body className={cn(fontSans.variable, fontMono.variable, "font-sans antialiased")}>`.

---

## 10. Routing & Auth Guard

### 10.1 Segments

- `(auth)` — `login`, `onboarding/credentials`. No shell. Single-column,
  centered.
- `(operator)` — everything else. Wrapped in `AppShell`. Server-side guard
  redirects to `/login` if JWT missing/expired.

### 10.2 Auth bootstrap (server-side)

`lib/auth/bootstrap.ts`:

```ts
import { cookies } from "next/headers";
import { decodeJwtPayload } from "@/lib/auth/jwt";

export async function authBootstrap() {
  const jwt = cookies().get("pcr.jwt")?.value;
  if (!jwt) return { ok: false as const };
  const payload = decodeJwtPayload(jwt);
  if (!payload || payload.exp * 1000 < Date.now()) return { ok: false as const };
  return { ok: true as const, jwt, user: { id: payload.sub, username: payload.username, role: payload.role } };
}
```

For SPA-only deployments, replace cookie reads with `sessionStorage`
hydration in a client wrapper. The chosen storage is recorded in
`05_State_and_Data.md`.

---

## 11. Layout Shifts (avoid)

- Reserve sidebar width with a CSS variable `--sidebar-w` set on the
  parent. Switching collapse only changes that variable; nothing else
  reflows.
- Top bar always renders all clusters; if data is loading, render
  `Skeleton` placeholders of the same width.
- Tables reserve column widths via `<col>` elements rather than letting
  content drive them.

---

## 12. Done = These Tests Pass

- Storybook (or `pnpm dev` smoke) renders AppShell with three themes
  without console errors.
- Cypress / Playwright e2e: login → AppShell mounts → SideNav present →
  CommandMenu opens with `⌘K` → ThemeMenu cycles all three themes.
- No `console.warn` about missing `aria-*` attributes.

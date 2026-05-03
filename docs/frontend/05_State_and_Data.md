# State & Data

How the frontend gets data, where it stores it, and how it stays in sync
with the backend. This is **the** doc to read before writing anything in
`lib/`.

---

## 1. Data Sources Overview

| Source | Transport | Used for |
|---|---|---|
| Live views | WebSocket `/stream` | Dashboard tiles, per-index cards, position live data, ΔPCR live, configs view, health view |
| Historical reads | REST GET | Position history, reports, PnL history, ΔPCR history, analytics charts |
| Mutations | REST POST/PUT/DELETE | Login, logout, halt/resume, kill, manual exit, config save, credentials, instrument refresh |

**Rule of thumb**: if the data updates during the day, it comes from the
WebSocket. If the data is queried by date range, it comes from REST.

---

## 2. Stores

State lives in **Zustand** stores. One store per domain. Stores never
fetch data directly — they hold state. Hooks at the page/component level
read from stores and call services for mutations.

### 2.1 `authStore`

```ts
// lib/stores/authStore.ts
type AuthState = {
  jwt: string | null;
  user: { id: string; username: string; role: string } | null;
  expiresAt: number | null;       // ms epoch
  setSession: (s: { jwt: string; user: AuthState["user"]; expiresAt: number }) => void;
  clear: () => void;
};
```

- Persists to `sessionStorage` under `pcr.auth`.
- The auth bootstrap on app start hydrates this from storage.
- A token-refresh timer (60 s before `expiresAt`) calls `POST /auth/refresh`
  and updates the session.

### 2.2 `viewsStore`

```ts
// lib/stores/viewsStore.ts
type ViewsState = {
  byKey: Partial<Record<ViewKey, ViewPayloadByKey[ViewKey]>>;
  set<K extends ViewKey>(key: K, value: ViewPayloadByKey[K]): void;
  clear(): void;
};
```

- Keyed exactly by the view names from `Frontend_Basics.md` §5: `dashboard`,
  `position:nifty50`, `position:banknifty`, `positions:closed_today`,
  `strategy:nifty50`, `strategy:banknifty`, `delta_pcr:nifty50`,
  `delta_pcr:banknifty`, `pnl`, `capital`, `health`, `configs`.
- Only the WS layer writes to this store. Components read via selectors.
- On reconnect, the `set` calls from the snapshot frame replace previous
  values atomically (no merge).

### 2.3 `themeStore`

```ts
type ThemeState = {
  theme: "slate-dark" | "carbon-dark" | "operator-light" | "auto";
  effectiveTheme: "slate-dark" | "carbon-dark" | "operator-light";
  setTheme: (t: ThemeState["theme"]) => void;
};
```

- `effectiveTheme` resolves "auto" against `prefers-color-scheme`.
- A small effect writes `data-theme` to `<html>` whenever `effectiveTheme`
  changes.
- Persists to `localStorage` under `pcr.theme`.

### 2.4 `uiStore`

```ts
type UiState = {
  sidebarCollapsed: boolean;
  density: "default" | "compact";
  toggleSidebar: () => void;
  setDensity: (d: "default" | "compact") => void;
};
```

- Persists to `localStorage` under `pcr.ui`.

### 2.5 `commandStore`

```ts
type CommandState = {
  open: boolean;
  setOpen: (v: boolean) => void;
  toggle: () => void;
};
```

Drives the CommandMenu visibility from anywhere in the tree.

### 2.6 No `dataStore` for REST results

Historical fetches return data directly to the calling component. No
caching layer (no SWR, no TanStack Query). The operator visits these pages
infrequently; freshness matters more than cache-hit rate. If we add a cache
later, it goes here.

---

## 3. WebSocket Connection

### 3.1 Singleton

`lib/ws/connection.ts` exposes a singleton `WSClient` with:

```ts
type WSClient = {
  start(): void;
  stop(): void;
  subscribe(views: ViewKey[]): void;
  notifications$: Observer<Notification>;
  state$: Observer<WSConnectionState>;  // "connecting" | "open" | "stale" | "closed"
};
```

The singleton is mounted by `useWSConnection()` inside `AppShell`. Mounting
multiple times is a no-op.

### 3.2 Message types

```ts
type ServerMessage =
  | { type: "snapshot"; ts: string; data: Partial<Record<ViewKey, unknown>> }
  | { type: "update";   ts: string; view: ViewKey; data: unknown }
  | { type: "notification"; ts: string; level: "INFO" | "WARNING" | "CRITICAL"; msg: string }
  | { type: "ping"; ts: string };

type ClientMessage =
  | { type: "subscribe"; views: ViewKey[] }
  | { type: "pong" };
```

### 3.3 Subscriptions

The default subscription (sent on every successful open):

```ts
const DEFAULT_SUBSCRIPTIONS: ViewKey[] = [
  "dashboard",
  "position:nifty50",
  "position:banknifty",
  "positions:closed_today",
  "strategy:nifty50",
  "strategy:banknifty",
  "delta_pcr:nifty50",
  "delta_pcr:banknifty",
  "pnl",
  "capital",
  "health",
  "configs",
];
```

When the operator opens `/analytics`, the client appends nothing (analytics
data is REST-only).

### 3.4 Heartbeat

- Server sends `ping` every 20 s.
- Client replies with `pong` immediately.
- If client misses three pongs in a row (60 s without server activity),
  the client closes with code 4000 and reconnects.

### 3.5 Reconnect

`lib/ws/reconnect.ts` implements exponential backoff:

| Attempt | Delay |
|---|---|
| 1 | 0 ms (immediate) |
| 2 | 500 ms |
| 3 | 1 s |
| 4 | 2 s |
| 5 | 4 s |
| 6+ | 8 s (cap) |

After 12 consecutive failed attempts, surface a destructive banner with a
manual "Try again" button.

On every reconnect:
1. Reset attempt counter.
2. Re-send `subscribe` with `DEFAULT_SUBSCRIPTIONS`.
3. Wait for `snapshot` frame; replace `viewsStore` atomically.
4. Resume normal `update` processing.

### 3.6 Close codes

| Code | Action |
|---|---|
| 1000 (normal) | Reconnect with backoff |
| 1008 (auth invalid) | Try `POST /auth/refresh`. On success, reconnect. On failure, redirect to `/login`. |
| 1011 (server error) | Reconnect with backoff |
| 4000 (heartbeat timeout) | Reconnect immediately |
| 4001 (subscription validation) | Show toast, drop the offending view from subscriptions, retry |

### 3.7 Page lifecycle

- On `visibilitychange` to `hidden`, the client keeps the connection but
  pauses re-subscribing on focus.
- On `visibilitychange` back to `visible`, if connection state is `stale`,
  trigger a manual reconnect.

---

## 4. REST Client

### 4.1 Wrapper

`lib/api/client.ts`:

```ts
type RequestOptions = {
  method?: "GET" | "POST" | "PUT" | "DELETE";
  body?: unknown;
  query?: Record<string, string | number | boolean | undefined>;
  signal?: AbortSignal;
  auth?: boolean;       // default true; false for /auth/login
};

async function request<T>(path: string, options?: RequestOptions): Promise<T>;
```

Behaviour:
- Reads `NEXT_PUBLIC_API_BASE_URL` for base.
- Adds `Authorization: Bearer <jwt>` when `auth` is true.
- Serialises body via `JSON.stringify`.
- Parses JSON response.
- On non-2xx: throws `APIError` with `{ status, code, message, details }`
  matching the backend error envelope (`docs/API.md` §2).
- On 401 with `code === "TOKEN_EXPIRED"`: attempts a single `/auth/refresh`,
  retries the original request once. On second 401, clears the auth store
  and redirects to `/login`.

### 4.2 Endpoint helpers

`lib/api/endpoints.ts` exposes one typed function per backend endpoint.
Examples:

```ts
export const authApi = {
  login: (body: LoginRequest) => request<LoginResponse>("/auth/login", { method: "POST", body, auth: false }),
  refresh: () => request<LoginResponse>("/auth/refresh", { method: "POST" }),
};

export const positionsApi = {
  open:        () => request<PositionsOpenResponse>("/positions/open"),
  closedToday: () => request<PositionsClosedTodayResponse>("/positions/closed_today"),
  history:     (q: PositionsHistoryQuery) => request<PositionsHistoryResponse>("/positions/history", { query: q }),
  report:      (id: string) => request<PositionReport>(`/reports/${id}`),
  manualExit:  (id: string, body: { reason: string }) =>
    request<{ ok: true; queued: true; position_id: string }>(`/commands/manual_exit/${id}`, { method: "POST", body }),
};

export const strategyApi = {
  status:        () => request<StrategyStatusResponse>("/strategy/status"),
  haltIndex:     (index: string) => request("/commands/halt_index/" + index, { method: "POST" }),
  resumeIndex:   (index: string) => request("/commands/resume_index/" + index, { method: "POST" }),
  globalKill:    (body: { reason: string }) => request("/commands/global_kill", { method: "POST", body }),
  globalResume:  (body: { reason: string; reset_daily_loss_circuit?: boolean }) =>
    request("/commands/global_resume", { method: "POST", body }),
};

export const configsApi = {
  getAll:    () => request<ConfigsBundle>("/configs"),
  getOne:    (section: string) => request<unknown>(`/configs/${section}`),
  putOne:    (section: string, value: unknown) => request<{ ok: true; section: string; updated_at: string; value: unknown }>(`/configs/${section}`, { method: "PUT", body: value }),
};

export const credentialsApi = {
  get:    () => request<UpstoxCredsMasked>("/credentials/upstox"),
  upsert: (body: UpstoxCredentialsIn) => request<{ ok: true; auth_status: string; trading_active: boolean; trading_disabled_reason: string }>("/credentials/upstox", { method: "POST", body }),
  remove: () => request<{ ok: true; auth_status: string }>("/credentials/upstox", { method: "DELETE" }),
};

export const capitalApi = {
  funds:        () => request<CapitalFunds>("/capital/funds"),
  killSwitch:   () => request<KillSwitchResponse>("/capital/kill_switch"),
  setKill:      (body: KillSwitchRequest) => request<KillSwitchResponse>("/capital/kill_switch", { method: "POST", body }),
};

export const commandsApi = {
  instrumentRefresh: () => request<{ ok: true; indexes: Record<string, unknown> }>("/commands/instrument_refresh", { method: "POST" }),
  upstoxTokenRequest: () => request<{ ok: true; authorization_expiry?: string; notifier_url?: string; message: string }>("/commands/upstox_token_request", { method: "POST" }),
};

export const pnlApi = {
  live:    () => request<PnlLive>("/pnl/live"),
  history: (q: PnlHistoryQuery) => request<PnlHistoryResponse>("/pnl/history", { query: q }),
};

export const deltaPcrApi = {
  live:    (index: string) => request<DeltaPcrLive>(`/delta_pcr/${index}/live`),
  history: (index: string, q: DateRange) => request<DeltaPcrHistory>(`/delta_pcr/${index}/history`, { query: q }),
  setMode: (index: string, body: { mode: 1 | 2 | 3 }) => request<{ ok: true; index: string; mode: number }>(`/delta_pcr/${index}/mode`, { method: "PUT", body }),
};

export const healthApi = {
  get:           () => request<HealthView>("/health", { auth: false }),
  depsTest:      () => request<DepsTestResult>("/health/dependencies/test"),
};

// Phase 10b
export const analyticsApi = {
  optionChain: (index: string, q: AnalyticsOptionChainQuery) => request<AnalyticsOptionChainResponse>(`/analytics/option_chain/${index}`, { query: q }),
  snapshots:   (index: string, q: AnalyticsSnapshotsQuery) => request<AnalyticsSnapshotsResponse>(`/analytics/snapshots/${index}`, { query: q }),
  strategy:    (index: string, q: DateRange) => request<AnalyticsStrategyResponse>(`/analytics/strategy/${index}`, { query: q }),
};
```

### 4.3 Mock mode

`NEXT_PUBLIC_API_BASE_URL=__mock__` activates `lib/api/mockClient.ts`, which
replays canned JSON responses for offline UI work. Mock fixtures live in
`tests/fixtures/api/`.

---

## 5. Type Mirrors

### 5.1 View payloads

`lib/types/views.ts` mirrors `docs/Frontend_Basics.md` §5 verbatim:

```ts
export type DashboardView = {
  system_state: { mode: "paper" | "live"; trading_active: boolean };
  total_pnl_today: number;
  open_positions_count: number;
  trades_today: number;
  win_rate_today: number;
  indexes: Record<"nifty50" | "banknifty", IndexSummary>;
  health_summary: "OK" | "DEGRADED" | "DOWN";
  ts: string;
};

export type PositionView = {
  id: string;
  index: "nifty50" | "banknifty";
  side: "CE" | "PE";
  entry_at: string;
  entry_premium: number;
  current_premium: number;
  running_pnl: number;
  quantity: number;
  token: string;
  strike: number;
  ts: string;
} | null;

// ... and so on for every key listed in Frontend_Basics.md §5
```

### 5.2 REST shapes

`lib/types/api.ts` mirrors `docs/API.md` §3–§10. Every request body and
response payload has a typed alias. Where backend uses Pydantic, the
frontend uses zod for runtime validation **only on dangerous inputs**
(login, credentials, configs save). Other endpoints trust the contract.

### 5.3 Single source of types

If the backend changes a payload shape, **the docs change first**, then the
type mirrors. Don't reverse-engineer from a successful response in dev.

---

## 6. Number / Date Utilities

`lib/utils/format.ts`:

```ts
export function formatINR(n: number, opts?: { showSign?: boolean; precision?: number }): string;
export function formatPct(n: number, opts?: { showSign?: boolean; precision?: number }): string;
export function formatPremium(n: number, precision?: number): string;
export function formatStrike(n: number): string;
export function formatOI(n: number): string;
export function formatDuration(ms: number): string;       // "14m 23s"
export function formatA11y(n: number, kind: "money" | "pct"): string;
```

`lib/utils/ist.ts`:

```ts
export function nowIst(): Date;
export function toIstIso(d: Date): string;
export function fromIstIso(s: string): Date;
export function formatIstClock(d: Date | string): string; // "11:42:31"
export function formatIstDate(d: Date | string): string;  // "28 Apr 2026"
```

All times displayed in IST. The backend already emits ISO with the
`+05:30` offset; we never strip or reapply it.

---

## 7. Errors

### 7.1 `APIError`

```ts
export class APIError extends Error {
  status: number;
  code: string;
  details?: unknown;
  constructor(status: number, code: string, message: string, details?: unknown) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
  }
}
```

### 7.2 Handling

- Inside event handlers: `try { ... } catch (e) { toast.error(unpack(e)) }`.
- Inside data fetchers (page-level): rethrow; the page boundary renders
  `ErrorState`.
- Inside form `onSubmit`: map `e.code` to a known field-level error if
  possible (e.g. `INVALID_CREDENTIALS` → top of form), otherwise toast.

### 7.3 Common code reactions

| `error.code` | Reaction |
|---|---|
| `AUTH_REQUIRED` | Redirect to `/login` |
| `TOKEN_EXPIRED` | Try refresh; if fails, redirect to `/login` |
| `TOKEN_INVALID` | Clear auth, redirect to `/login` |
| `RATE_LIMITED` | Toast "Slow down — try again in a minute" |
| `VALIDATION_ERROR` | Toast with `details.errors[0].msg` |
| `INDEX_NOT_FOUND` | Toast "Unknown index" |
| `POSITION_NOT_FOUND` | Toast "Position no longer open" |
| `BROKER_ERROR` | Toast with `details.broker.code` if present |
| Anything else | Toast with `message` |

---

## 8. Auth Lifecycle

### 8.1 Login

```
LoginForm.submit
  → authApi.login(body)
  → authStore.setSession({ jwt, user, expiresAt })
  → cookie set: pcr.jwt (HttpOnly via API route or sessionStorage if SPA)
  → router.push("/dashboard")
  → AppShell mounts → useWSConnection() → WS opens
```

### 8.2 Refresh

A timer in `authStore` schedules `setTimeout(refresh, expiresAt - now - 60_000)`.

```
refresh
  → authApi.refresh()
  → authStore.setSession(...)
  → if WS is open: do nothing (token in URL is for handshake only; server
    holds the session reference)
  → if WS is closed: trigger reconnect (which uses the new token)
```

### 8.3 Logout

```
UserMenu.signOut
  → authStore.clear()
  → wsClient.stop()
  → router.replace("/login")
```

### 8.4 401 mid-session

```
APIError(401, TOKEN_EXPIRED)
  → try authApi.refresh()
  → on success: replay original request once
  → on failure: authStore.clear() → router.replace("/login")
```

---

## 9. Selectors

Components should read narrow slices, not the whole store:

```ts
// good
const dashboard = useViewsStore((s) => s.byKey["dashboard"] as DashboardView | undefined);

// bad: re-renders on any view update
const all = useViewsStore((s) => s.byKey);
```

For frequently-read tuples, define helpers:

```ts
export const useDashboardView = () =>
  useViewsStore((s) => s.byKey["dashboard"] as DashboardView | null);

export const usePositionView = (index: "nifty50" | "banknifty") =>
  useViewsStore((s) => s.byKey[`position:${index}` as const] as PositionView);
```

---

## 10. Testing State

- Unit-test stores with `vitest`. Reset stores between tests via a helper
  `resetAllStores()` exported from `lib/stores/index.ts`.
- Mock `WSClient` for component tests: provide a `setSnapshot()` and
  `pushUpdate()` API on the test double.
- Use `lib/api/mockClient.ts` fixtures for happy-path REST flows.
- Test the error map (§7.3) with table-driven tests.

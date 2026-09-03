/**
 * Typed admin API client (AD-01-12).
 *
 * Every admin surface talks to the FastAPI backend through this module so
 * auth headers, the base URL, and error semantics live in exactly one place.
 *
 * The admin endpoints are protected by the shared admin bearer token (the
 * "admin password" gate from AD-01-01). The token is read from
 * `localStorage.admin_token`, falling back to NEXT_PUBLIC_ADMIN_PASSWORD,
 * matching the behaviour the legacy admin pages already implement.
 */

export const ADMIN_API_BASE: string =
  process.env.NEXT_PUBLIC_API_BASE ?? "/api/v1";

export class AdminApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "AdminApiError";
    this.status = status;
  }
}

/** Auth token resolution shared by every admin page. */
export function getAdminToken(): string {
  const stored =
    typeof window !== "undefined" ? localStorage.getItem("admin_token") : null;
  const token =
    stored || process.env.NEXT_PUBLIC_ADMIN_PASSWORD || "sailfast2026";
  if (typeof window !== "undefined" && !stored) {
    localStorage.setItem("admin_token", token);
  }
  return token;
}

/** Clear a rejected token so the next request re-prompts for a password. */
export function clearAdminToken(): void {
  if (typeof window !== "undefined") {
    localStorage.removeItem("admin_token");
  }
}

export interface AdminRequestOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  body?: unknown;
  headers?: Record<string, string>;
  signal?: AbortSignal;
}

/**
 * Typed fetch wrapper for `/admin/*` endpoints. Throws `AdminApiError` on
 * non-2xx responses; clears a stale token on 401/403 so pages fall back to
 * their password gate.
 */
export async function adminFetch<T>(
  path: string,
  options: AdminRequestOptions = {},
): Promise<T> {
  const { method = "GET", body, headers = {}, signal } = options;

  const res = await fetch(`${ADMIN_API_BASE}${path}`, {
    method,
    signal,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${getAdminToken()}`,
      ...headers,
    },
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
  });

  if (!res.ok) {
    if (res.status === 401 || res.status === 403) {
      clearAdminToken();
    }
    let detail = `Request failed: ${res.status}`;
    try {
      const payload = (await res.json()) as { detail?: string };
      if (payload?.detail) detail = payload.detail;
    } catch {
      /* non-JSON error body — keep the generic message */
    }
    throw new AdminApiError(res.status, detail);
  }

  return (await res.json()) as T;
}

/* ── AD-01-13 · GET /v1/admin/overview ──────────────────────────────────
   Drives the sidebar counts + topbar health pills. Until the endpoint
   exists (AD-01-13) the shell renders zeroed counts. */

export interface AdminOverviewCounts {
  today: number;
  data_quality: number;
  operations: number;
  customers: number;
  agents: number;
}

export type AdminHealthStatus = "ok" | "warn" | "down" | "unknown";

export interface AdminHealthPill {
  label: string;
  status: AdminHealthStatus;
  detail?: string;
}

export interface AdminOverview {
  counts: AdminOverviewCounts;
  health: AdminHealthPill[];
  environment?: string;
}

export const EMPTY_ADMIN_OVERVIEW: AdminOverview = {
  counts: { today: 0, data_quality: 0, operations: 0, customers: 0, agents: 0 },
  health: [],
};

/**
 * Coerce whatever /admin/overview returns into the shell's contract.
 *
 * AD-01-12 (this shell) expects `{ counts, health }`. AD-01-13 shipped
 * /v1/admin/overview with a different payload entirely (schema_version,
 * today, sources, dupes, attention, …) and no `counts` or `health` key. The
 * old blind cast therefore stored an object whose `counts` was undefined,
 * and AdminSidebar's `counts[section.key]` threw — white-screening every
 * admin page. Normalising here keeps the documented "counts render 0"
 * behaviour instead of crashing when the shapes disagree.
 */
function normaliseOverview(raw: unknown): AdminOverview {
  const src = (raw ?? {}) as Partial<AdminOverview>;
  const counts = src.counts;
  const health = src.health;
  return {
    counts:
      counts && typeof counts === "object"
        ? { ...EMPTY_ADMIN_OVERVIEW.counts, ...counts }
        : { ...EMPTY_ADMIN_OVERVIEW.counts },
    health: Array.isArray(health) ? health : [],
    ...(typeof src.environment === "string"
      ? { environment: src.environment }
      : {}),
  };
}

/** Fetch the admin overview; returns zeroed counts when AD-01-13 is absent. */
export async function fetchAdminOverview(
  signal?: AbortSignal,
): Promise<AdminOverview> {
  try {
    return normaliseOverview(
      await adminFetch<unknown>("/admin/overview", { signal }),
    );
  } catch (err) {
    // 404 = endpoint not shipped yet (AD-01-13); render 0s per the contract.
    if (err instanceof AdminApiError && err.status === 404) {
      return EMPTY_ADMIN_OVERVIEW;
    }
    throw err;
  }
}

/* ── GET /v1/health — public service health used for topbar pills ─────── */

export interface ServiceHealth {
  status?: string;
  database?: string;
  counts?: Record<string, number>;
  freshness?: Record<string, string | null>;
  [key: string]: unknown;
}

export async function fetchServiceHealth(
  signal?: AbortSignal,
): Promise<ServiceHealth> {
  const res = await fetch(`${ADMIN_API_BASE}/health`, { signal });
  if (!res.ok) {
    throw new AdminApiError(res.status, `Health check failed: ${res.status}`);
  }
  return (await res.json()) as ServiceHealth;
}

export const adminApi = {
  baseUrl: ADMIN_API_BASE,
  fetch: adminFetch,
  getToken: getAdminToken,
  clearToken: clearAdminToken,
  overview: fetchAdminOverview,
  health: fetchServiceHealth,
};

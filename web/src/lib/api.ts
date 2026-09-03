const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? "/api/v1";

/* ── Types ────────────────────────────────────────────────────────────── */

export interface SearchResult {
  id: number;
  boat_name: string;
  sail_number: string;
  design: string | null;
  country: string | null;
  year_built: number | null;
  score: number;
}

export interface SearchSuggestion {
  id: number;
  boat_name: string;
  sail_number: string;
  design: string | null;
  country: string | null;
  year_built: number | null;
}

export interface SearchResponse {
  query: string;
  total: number;
  limit: number;
  offset: number;
  results: SearchResult[];
  suggestions?: SearchSuggestion[];
}

export interface BoatDetail {
  id: number;
  boat_name: string;
  sail_number: string;
  cert_number?: string;
  design: string | null;
  design_canonical?: string | null;
  country: string | null;
  year_built?: number | null;
  builder?: string | null;
  designer?: string | null;
  /* IRC rating fields */
  irc_tcc?: number | null;
  irc_non_spi_tcc?: number | null;
  irc_crew?: number | null;
  irc_lh?: number | null;
  irc_beam?: number | null;
  irc_draft?: number | null;
  irc_snapshot_date?: string | null;
  irc_endorsed?: boolean | null;
  /* ORC fields */
  orc_gph?: number | null;
  orc_cdl?: number | null;
  /* Physical */
  loa?: number | null;
  lwl?: number | null;
  beam_max?: number | null;
  displacement_kg?: number | null;
  [key: string]: unknown;
}

export interface SSEStep {
  label: string;
  detail?: string;
}

export interface SSEEvent {
  type: "text" | "done" | "error" | "step" | "thought_chunk" | "phase";
  data: string | SSEStep | Record<string, unknown>;
}

export interface CheckoutSessionResponse {
  checkout_url: string;
  order_token: string;
}

export interface Recommendation {
  rank: number;
  field: string;
  category: string;
  current_value: number | string;
  mean_value: number | string;
  tcc_delta: number;
  feasibility: string;
  evidence_strength: string;
  explanation: string;
}

export interface RAI {
  rai_score: number;
  n_races: number;
  wins: number;
  podiums: number;
  ci_low: number;
  ci_high: number;
  interpretation: string;
}

export interface Rival {
  boat_name: string;
  sail_number: string;
  wins: number;
  losses: number;
  win_rate: number;
  events: number;
}

export interface ReportData {
  status: "pending" | "paid" | "generated" | "ready" | "error";
  boat?: BoatDetail;
  report_markdown?: string;
  recommendations?: Recommendation[];
  rai?: RAI | null;
  rivals?: Rival[];
}

/* ── Stats (OPS-02-11: marketing numbers come from the DB census) ─────── */

export interface StatsResponse {
  /* Flat count keys (legacy-compatible) */
  boats: number;
  tcc_snapshots: number;
  irc_certificates: number;
  orc_certificates: number;
  race_results: number;
  events: number;
  countries: number;
  designs: number;
  sources: number;
  /* Structured views */
  counts: Record<string, number>;
  last_updated: Record<string, string | null>;
  generated_at: string;
  cache_ttl_seconds: number;
}

/** Format a census count the way marketing copy reads ("258,000"). */
export function formatStatCount(n: number): string {
  return Math.floor(n / 1000) * 1000 >= 1000
    ? (Math.floor(n / 1000) * 1000).toLocaleString("en-US")
    : n.toLocaleString("en-US");
}

export async function getStats(): Promise<StatsResponse> {
  const res = await fetch(`${API_BASE}/stats/`, {
    method: "GET",
    headers: { Accept: "application/json" },
  });

  if (!res.ok) {
    throw new Error(`Stats fetch failed: ${res.status}`);
  }

  return res.json();
}

/* ── Search ───────────────────────────────────────────────────────────── */

export async function searchBoats(query: string): Promise<SearchResult[]> {
  const data = await searchBoatsFull(query);
  return data.results;
}

export async function searchBoatsFull(query: string): Promise<SearchResponse> {
  if (!query || query.trim().length < 2) {
    return { query, total: 0, limit: 20, offset: 0, results: [], suggestions: [] };
  }

  const res = await fetch(
    `${API_BASE}/search?q=${encodeURIComponent(query.trim())}`,
    {
      method: "GET",
      headers: { Accept: "application/json" },
    }
  );

  if (!res.ok) {
    throw new Error(`Search failed: ${res.status}`);
  }

  return res.json();
}

/* ── Boat Detail ──────────────────────────────────────────────────────── */

export async function getBoat(id: number): Promise<BoatDetail> {
  const res = await fetch(`${API_BASE}/boats/${id}`, {
    method: "GET",
    headers: { Accept: "application/json" },
  });

  if (!res.ok) {
    throw new Error(`Boat fetch failed: ${res.status}`);
  }

  return res.json();
}

/* ── Owner Corrections ────────────────────────────────────────────────── */

export type CorrectionField =
  | "designer"
  | "builder"
  | "year_built"
  | "design_canonical"
  | "new_design_class";

export interface CorrectionSubmission {
  field_name: CorrectionField;
  proposed_value: string;
  submitted_email?: string;
}

export async function submitCorrection(
  boatId: number,
  body: CorrectionSubmission,
): Promise<{ id: number; status: string }> {
  const res = await fetch(`${API_BASE}/boats/${boatId}/corrections`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new Error(`Correction submit failed (${res.status}): ${detail.slice(0, 200)}`);
  }
  return res.json();
}

/* ── SSE Streaming Insights ───────────────────────────────────────────── */

export async function* streamInsights(
  boatId: number,
  detailLevel: "free" | "premium" = "free",
  thinkingStyle: "steps" | "prose" = "steps"
): AsyncGenerator<SSEEvent, void, unknown> {
  const res = await fetch(`${API_BASE}/insights/ask`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
    },
    body: JSON.stringify({
      boat_id: boatId,
      question:
        detailLevel === "free"
          ? "Analyse this boat"
          : "Full optimisation report. Where am I giving away rating and what should I change first?",
      detail_level: detailLevel,
      thinking_style: thinkingStyle,
    }),
  });

  if (!res.ok) {
    throw new Error(`Insights request failed: ${res.status}`);
  }

  const reader = res.body?.getReader();
  if (!reader) {
    throw new Error("No readable stream in response");
  }

  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });

      const lines = buffer.split("\n");
      // Keep the last potentially incomplete line in the buffer
      buffer = lines.pop() ?? "";

      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed || !trimmed.startsWith("data:")) continue;

        const jsonStr = trimmed.slice(5).trim();
        if (!jsonStr || jsonStr === "[DONE]") continue;

        try {
          const event: SSEEvent = JSON.parse(jsonStr);
          yield event;

          if (event.type === "done") {
            return;
          }
        } catch {
          // Skip malformed JSON lines
        }
      }
    }

    // Process any remaining buffer
    if (buffer.trim()) {
      const trimmed = buffer.trim();
      if (trimmed.startsWith("data:")) {
        const jsonStr = trimmed.slice(5).trim();
        if (jsonStr && jsonStr !== "[DONE]") {
          try {
            const event: SSEEvent = JSON.parse(jsonStr);
            yield event;
          } catch {
            // Skip
          }
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
}

/* ── Checkout Session ────────────────────────────────────────────────── */

export async function createCheckoutSession(params: {
  boat_id: number;
  boat_name: string;
  currency: string;
  search_query?: string;
  teaser_text?: string;
  /**
   * Clerk session token for signed-in buyers (PAY-01-08). When present the
   * API reuses the user's single Stripe customer; when absent the session
   * is created with customer_creation=always.
   */
  authToken?: string | null;
}): Promise<CheckoutSessionResponse> {
  const { authToken, ...body } = params;
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    Accept: "application/json",
  };
  if (authToken) {
    headers.Authorization = `Bearer ${authToken}`;
  }

  const res = await fetch(`${API_BASE}/checkout/create-session`, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });

  if (!res.ok) {
    throw new Error(`Checkout failed: ${res.status}`);
  }

  return res.json();
}

/* ── Report ──────────────────────────────────────────────────────────── */

export async function getReport(token: string): Promise<ReportData> {
  const res = await fetch(`${API_BASE}/reports/${token}`, {
    method: "GET",
    headers: { Accept: "application/json" },
  });

  if (!res.ok) {
    throw new Error(`Report fetch failed: ${res.status}`);
  }

  return res.json();
}

export function getReportPdfUrl(token: string): string {
  return `${API_BASE}/reports/${token}/pdf`;
}

/* ── Survey ──────────────────────────────────────────────────────────── */

export async function submitSurvey(params: {
  order_token: string;
  usefulness_score: number | null;
  newsletter_signup: boolean;
  user_type: string | null;
  missing_info?: string;
  email?: string;
}): Promise<{ status: string; message: string }> {
  const res = await fetch(`${API_BASE}/surveys/submit`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
    },
    body: JSON.stringify(params),
  });

  if (!res.ok) {
    throw new Error(`Survey submit failed: ${res.status}`);
  }

  return res.json();
}

/* ── Signed-in user ───────────────────────────────────────────────────── */

export interface CurrentUser {
  id: number;
  clerk_id: string;
  email?: string | null;
  role?: string | null;
  plan?: string | null;
  subscription_status?: string | null;
  stripe_customer_id?: string | null;
}

/**
 * Mirror the signed-in Clerk identity into our own `users` table.
 *
 * Clerk owns authentication, but a Clerk user only becomes a row on our side
 * when something calls this. Until it was added, that happened solely inside
 * checkout, so signed-in visitors stayed invisible until they tried to pay.
 * Idempotent — safe to call on every load.
 */
export async function syncCurrentUser(
  authToken: string,
): Promise<CurrentUser | null> {
  const res = await fetch(`${API_BASE}/users/me`, {
    headers: {
      Accept: "application/json",
      Authorization: `Bearer ${authToken}`,
    },
  });
  if (!res.ok) return null;
  return (await res.json()) as CurrentUser;
}

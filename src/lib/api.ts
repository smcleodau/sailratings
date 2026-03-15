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

export interface SearchResponse {
  query: string;
  total: number;
  limit: number;
  offset: number;
  results: SearchResult[];
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

export interface SSEEvent {
  type: "text" | "done" | "error";
  data: string;
}

export interface CheckoutSessionResponse {
  checkout_url: string;
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
  status: "pending" | "paid" | "ready" | "error";
  boat?: BoatDetail;
  report_markdown?: string;
  recommendations?: Recommendation[];
  rai?: RAI | null;
  rivals?: Rival[];
}

/* ── Search ───────────────────────────────────────────────────────────── */

export async function searchBoats(query: string): Promise<SearchResult[]> {
  if (!query || query.trim().length < 2) return [];

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

  const data: SearchResponse = await res.json();
  return data.results ?? [];
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

/* ── SSE Streaming Insights ───────────────────────────────────────────── */

export async function* streamInsights(
  boatId: number,
  detailLevel: "free" | "premium" = "free"
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
}): Promise<CheckoutSessionResponse> {
  const res = await fetch(`${API_BASE}/checkout/create-session`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
    },
    body: JSON.stringify(params),
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

/**
 * Corrections moderation client (AD-01-09).
 *
 *   GET  /v1/admin/corrections?status=&limit=   — moderation queue
 *   POST /v1/admin/corrections/{id}/approve     — apply the proposed value
 *   POST /v1/admin/corrections/{id}/reject      — reject, no data change
 *   POST /v1/admin/corrections/{id}/revert      — undo an applied correction
 *
 * Kept out of adminApi.ts (shared, append-only) — rides on the same
 * `adminFetch` helper so auth/error semantics are identical.
 */

import { adminFetch } from "./adminApi";

export type CorrectionStatus = "pending" | "applied" | "rejected" | "reverted";

export interface Correction {
  id: number;
  boat_id: number | null;
  boat_name: string | null;
  field_name: string;
  current_value: string | null;
  proposed_value: string;
  submitted_email: string | null;
  submitted_at: string;
  status: string;
  reviewed_at?: string | null;
  review_notes?: string | null;
}

export type CorrectionAction = "approve" | "reject" | "revert";

export interface CorrectionActionResult {
  status: string;
  id: number;
  restored_value?: string | null;
}

export async function fetchCorrections(
  status: CorrectionStatus,
  limit = 200,
  signal?: AbortSignal,
): Promise<Correction[]> {
  return adminFetch<Correction[]>(
    `/admin/corrections?status=${encodeURIComponent(status)}&limit=${limit}`,
    { signal },
  );
}

export async function actOnCorrection(
  id: number,
  action: CorrectionAction,
  reviewNotes?: string | null,
): Promise<CorrectionActionResult> {
  return adminFetch<CorrectionActionResult>(
    `/admin/corrections/${id}/${action}`,
    { method: "POST", body: { review_notes: reviewNotes ?? null } },
  );
}

export const approveCorrection = (id: number, notes?: string | null) =>
  actOnCorrection(id, "approve", notes);
export const rejectCorrection = (id: number, notes?: string | null) =>
  actOnCorrection(id, "reject", notes);
export const revertCorrection = (id: number, notes?: string | null) =>
  actOnCorrection(id, "revert", notes);

/** True when boat_corrections holds no rows in ANY status. */
export async function isCorrectionsTableEmpty(
  signal?: AbortSignal,
): Promise<boolean> {
  const others: CorrectionStatus[] = ["applied", "rejected", "reverted"];
  const results = await Promise.all(
    others.map((s) => fetchCorrections(s, 1, signal)),
  );
  return results.every((r) => r.length === 0);
}

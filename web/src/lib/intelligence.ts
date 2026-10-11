/**
 * IN-01-03 - types for the intelligence query service (SPEC-25 §3).
 * Mirrors api/src/irc_data/api/schemas/intelligence.py::ConfidenceAssessmentV1.
 */

export type ConfidenceBand = "high" | "medium" | "low";

export interface ConfidenceFactor {
  name: string;
  weight: number;
  /** 0..1 */
  value: number;
  note: string;
}

export interface ConfidenceAssessmentV1 {
  /** 0..1, sum of weight * value over the factors */
  score: number;
  band: ConfidenceBand;
  factors: ConfidenceFactor[];
  /** one entry per factor with value < 0.5 */
  limitations: string[];
}

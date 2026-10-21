import type { ConfidenceAssessmentV1, ConfidenceBand } from "@/lib/intelligence";

/**
 * IN-01-03 - confidence as a number plus named limitations (SPEC-25 §3).
 * Mono score and a band pill (Starboard / Buoy / #F0806A). The limitations
 * are a native `title` tooltip - no icon library. Mounted by IN-01-04.
 */

const BAND_COLOUR: Record<ConfidenceBand, string> = {
  high: "var(--sr-starboard, #3d9e6e)",
  medium: "var(--sr-buoy, #e8b23a)",
  low: "#F0806A",
};

export function confidenceTitle(confidence: ConfidenceAssessmentV1): string {
  const head = `Confidence ${confidence.score.toFixed(2)} (${confidence.band})`;
  if (confidence.limitations.length === 0) return `${head}. No limitations.`;
  return `${head}. Limitations:\n${confidence.limitations.map((l) => `- ${l}`).join("\n")}`;
}

export default function ConfidenceBadge({
  confidence,
  className = "",
}: {
  confidence: ConfidenceAssessmentV1;
  className?: string;
}) {
  const colour = BAND_COLOUR[confidence.band];
  return (
    <span
      className={`inline-flex items-center gap-2 ${className}`}
      title={confidenceTitle(confidence)}
      data-testid="confidence-badge"
    >
      <span
        className="text-[13px]"
        style={{ fontFamily: 'var(--sr-font-data, "JetBrains Mono", monospace)' }}
      >
        {confidence.score.toFixed(2)}
      </span>
      <span
        className="text-[9px] uppercase tracking-[0.1em] px-1.5 py-0.5 border rounded-[3px]"
        style={{
          fontFamily: 'var(--sr-font-data, "JetBrains Mono", monospace)',
          color: colour,
          borderColor: colour,
        }}
      >
        {confidence.band}
      </span>
    </span>
  );
}

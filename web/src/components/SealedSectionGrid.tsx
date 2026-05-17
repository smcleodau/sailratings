import { Lock } from "lucide-react";

export type SealedSection = {
  num: number;
  title: string;
  description: string;
};

export const SEALED_SECTIONS: SealedSection[] = [
  {
    num: 2,
    title: "Rating Drift",
    description:
      "How your TCC has moved across every IRC formula revision since you've owned the boat.",
  },
  {
    num: 3,
    title: "Measurement Sensitivity",
    description:
      "Regression on every certificate input — which measurements are quietly costing you tenths.",
  },
  {
    num: 4,
    title: "Fleet Performance",
    description:
      "Racing Advantage Index against your actual results: what the rating predicts vs what you sail.",
  },
  {
    num: 5,
    title: "Sister Boats",
    description:
      "Side-by-side with every boat of your design on the register — where yours rates light, where it rates heavy.",
  },
  {
    num: 6,
    title: "Head-to-Head Rivals",
    description:
      "The boats within ±0.005 TCC you're scored against most weekends, and how their certificates differ from yours.",
  },
  {
    num: 7,
    title: "Trial Certificate Model",
    description:
      "Re-rating scenarios costed out: does a re-measure actually move you, or are you near the floor.",
  },
  {
    num: 8,
    title: "Action Plan",
    description:
      "Ranked, specific measurement changes — what to do before your next certificate, in order of TCC return.",
  },
];

interface SealedSectionGridProps {
  /** How many cards to reveal (0..7). Cards beyond this count are not rendered.
   *  When undefined, all cards render immediately. */
  revealedCount?: number;
}

export default function SealedSectionGrid({ revealedCount }: SealedSectionGridProps = {}) {
  const count = revealedCount == null ? SEALED_SECTIONS.length : Math.min(revealedCount, SEALED_SECTIONS.length);
  const visible = SEALED_SECTIONS.slice(0, count);
  const allOut = count >= SEALED_SECTIONS.length;

  if (count <= 0) return null;

  return (
    <section className="px-6 sm:px-12 py-10 sm:py-12" aria-label="Sealed sections">
      <div className="flex items-baseline justify-between mb-6 border-b border-charcoal/15 pb-4">
        <div className="data-mono text-[10px] uppercase tracking-[0.18em] text-charcoal/70 font-semibold flex items-center gap-2">
          {allOut ? (
            <>Seven sections drafted · sealed pending order</>
          ) : (
            <>
              <span className="inline-block w-1.5 h-1.5 rounded-full bg-brass animate-pulse" />
              Drafting the rest of the file…
            </>
          )}
        </div>
        {allOut && (
          <div className="data-mono text-[10px] uppercase tracking-[0.18em] text-brass hidden sm:inline">
            §2 — §8
          </div>
        )}
      </div>
      <ol className="grid grid-cols-1 md:grid-cols-2 gap-4 sm:gap-5">
        {visible.map((sec) => (
          <li
            key={sec.num}
            className={`bg-cream border border-brass/30 hover:border-brass/60 transition-colors flex flex-col sealed-card-in ${
              sec.num === 8 ? "md:col-span-2 md:bg-brass/[0.06]" : ""
            }`}
          >
            <div className="px-5 pt-5 pb-4 flex-1">
              <div className="flex items-baseline gap-3 mb-2">
                <span className="data-mono text-brass text-[11px] font-semibold tracking-[0.1em]">
                  §{String(sec.num).padStart(2, "0")}
                </span>
                <span className="heading-display text-charcoal text-[15px] sm:text-[16px] font-semibold uppercase tracking-[0.02em]">
                  {sec.title}
                </span>
              </div>
              <p className="body-text text-charcoal/75 text-[13px] leading-[1.55]">
                {sec.description}
              </p>
            </div>
            <div className="bg-brass/15 border-t border-brass/30 px-5 py-2 flex items-center gap-2">
              <Lock size={11} strokeWidth={2} className="text-brass" />
              <span className="data-mono text-[10px] uppercase tracking-[0.16em] text-brass font-semibold">
                Sealed · §{sec.num} of 8
              </span>
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}

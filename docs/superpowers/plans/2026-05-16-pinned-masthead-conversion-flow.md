# Pinned Masthead Conversion Flow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current jolting, scroll-based bench flow with a pinned-masthead layout: sticky navy strip + boat masthead at the top, two-column bench (working-log left / prose right), brass-stamped sealed sections below, and a persistent charcoal CTA rail at the bottom. Mirror the masthead on `/report/[token]` and invert the report reveal so the recommendations table draws first.

**Architecture:** Three new React components extract the shared chrome (`PinnedMasthead`, `StickyCheckoutRail`, `SealedSectionGrid`). `TeaserAnalysis` composes them. `page.tsx` drops the broken `scrollIntoView` effect. `ReportView` adopts the same pinned masthead and re-orders its render. Dead `PurchaseCTA.tsx` is removed. New Playwright screenshot tests verify three beats on the bench plus the report payoff.

**Tech Stack:** Next.js 16, React 19 (client-rendered), Tailwind v4 with the existing navy/brass/cream/charcoal tokens, Söhne + Roboto Mono fonts, Playwright (already on disk in `web/tests/`).

**Spec:** `docs/superpowers/specs/2026-05-16-conversion-flow-pinned-masthead-design.md`

---

## File Plan

| Path | Status | Purpose |
|---|---|---|
| `web/src/components/PinnedMasthead.tsx` | new | Sticky navy hero strip + cream masthead row. Shared by bench + report. |
| `web/src/components/StickyCheckoutRail.tsx` | new | Persistent charcoal CTA rail at viewport bottom. |
| `web/src/components/SealedSectionGrid.tsx` | new | 2-col grid of brass-stamped sealed section cards. |
| `web/src/components/TeaserAnalysis.tsx` | modify | Switch to two-column bench, mount the new components, delete `RedactedSketch` + inline navy CTA + sticky pill. |
| `web/src/app/page.tsx` | modify | Drop `scrollIntoView`, render the new bench structure. |
| `web/src/components/ReportView.tsx` | modify | Mount `PinnedMasthead`, table-first reveal, 600ms "File compiled" moment. |
| `web/src/app/report/[token]/page.tsx` | modify | Pass boat through to `PinnedMasthead`; drop its own nav header. |
| `web/src/components/PurchaseCTA.tsx` | delete | Dead code — no imports. |
| `web/tests/screenshot-bench.mjs` | new | Three-beat capture: streaming, sealed-visible, after scroll. |
| `web/tests/screenshot-report.mjs` | new | Capture the table-first reveal on `/report/[token]`. |
| `web/tests/screenshot-search.mjs` | modify | Confirm search → bench is jump-free; capture the transition. |

**Conventions to honor:**
- Tailwind colors only via existing tokens (`bg-navy`, `text-brass`, `bg-cream`, `text-charcoal`).
- Typography classes: `.heading-display`, `.body-text`, `.data-mono`, `.brand-wordmark` (defined in `src/app/globals.css`).
- Playwright tests are pure node mjs scripts hit against the running dev server. Accept a URL argv with default `https://dev.sailratings.com/` so they can run against localhost during iteration.
- No emoji. No `✨`. No startup-vibes copy.
- Per `[[feedback-streaming-pacing]]`: never enumerate or count working steps.
- Per `[[feedback-testing]]`: every visual claim is verified with a Playwright screenshot before being called done.
- Per `[[feedback-pkill-next]]`: restart the next worker by killing its specific PID from `ss -tlnp | grep 4200`, never `pkill -f next.*4200`.

**Restart loop after any code change that affects the build:**

```bash
cd /home/irc-data/code/sailratings/web
ENVIRONMENT=dev npm run build
NEXT_PID=$(ss -tlnp 2>/dev/null | grep ':4200' | grep -oP 'pid=\K[0-9]+' | head -1)
if [ -n "$NEXT_PID" ]; then kill "$NEXT_PID"; sleep 1; fi
setsid nohup ./node_modules/.bin/next start -p 4200 \
  > /tmp/sailratings.log 2>&1 < /dev/null & disown
sleep 3
curl -s -o /dev/null -w "front=%{http_code}\n" --max-time 5 https://dev.sailratings.com/
```

---

## Task 1: Create `PinnedMasthead` component

**Files:**
- Create: `web/src/components/PinnedMasthead.tsx`

**Background:** This is the sticky chrome shared by the bench and the report page. It has two stacked rows — a 96-px navy strip with the wordmark + dateline, and a cream masthead row with the boat name, TCC, and inline-editable metadata. It does NOT contain the working log (that lives inside the bench panel). It must be `sticky top-0 z-30` so it stays pinned during scroll.

- [ ] **Step 1: Write the component file**

Create `web/src/components/PinnedMasthead.tsx` with the following content:

```tsx
"use client";

import { useMemo, useState } from "react";
import { Check, Loader2, Pencil, X } from "lucide-react";
import { submitCorrection, type BoatDetail, type CorrectionField } from "@/lib/api";

interface PinnedMastheadProps {
  boat: BoatDetail;
  /** Optional subline shown under the masthead row (e.g. "File compiled. Opening…"). */
  subline?: React.ReactNode;
}

function nowStamp(): string {
  const d = new Date();
  const day = String(d.getUTCDate()).padStart(2, "0");
  const mon = d.toLocaleString("en-GB", { month: "short", timeZone: "UTC" });
  const yr = d.getUTCFullYear();
  const hh = String(d.getUTCHours()).padStart(2, "0");
  const mm = String(d.getUTCMinutes()).padStart(2, "0");
  return `${day} ${mon} ${yr} · ${hh}:${mm} UTC`;
}

function EditableField({
  boatId,
  label,
  value,
  fieldName,
}: {
  boatId: number;
  label: string;
  value: string | number | null | undefined;
  fieldName: CorrectionField;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value != null ? String(value) : "");
  const [pending, setPending] = useState(false);
  const [submitted, setSubmitted] = useState(false);

  const send = async () => {
    const v = draft.trim();
    if (!v) return;
    setPending(true);
    try {
      await submitCorrection(boatId, { field_name: fieldName, proposed_value: v });
      setSubmitted(true);
      setEditing(false);
    } catch {
      // Moderation queue is best-effort.
    } finally {
      setPending(false);
    }
  };

  if (editing) {
    return (
      <span className="inline-flex items-center gap-1.5 align-baseline">
        <input
          autoFocus
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") void send();
            if (e.key === "Escape") setEditing(false);
          }}
          className="bg-cream border border-brass/50 text-charcoal px-2 py-0.5 text-[13px] font-body w-40 focus:outline-none focus:border-brass"
        />
        <button
          onClick={() => void send()}
          disabled={pending || !draft.trim()}
          className="text-brass hover:text-brass-dark disabled:opacity-30"
          aria-label="Submit correction"
        >
          {pending ? <Loader2 size={12} className="animate-spin" /> : <Check size={12} />}
        </button>
        <button
          onClick={() => setEditing(false)}
          className="text-charcoal/30 hover:text-charcoal"
          aria-label="Cancel"
        >
          <X size={12} />
        </button>
      </span>
    );
  }

  return (
    <span className="inline-flex items-baseline gap-1.5 group">
      <span className="data-mono text-[10px] uppercase tracking-[0.16em] text-charcoal/55 mr-0.5 font-semibold">
        {label}
      </span>
      <span className="body-text text-charcoal text-[13px]">
        {value != null && String(value).trim() !== "" ? value : (
          <span className="text-charcoal/40 italic">unknown</span>
        )}
      </span>
      {submitted ? (
        <span className="data-mono text-[9px] uppercase tracking-wider text-brass ml-1">
          pending review
        </span>
      ) : (
        <button
          onClick={() => setEditing(true)}
          className="text-brass/50 hover:text-brass transition-colors ml-1 inline-flex items-center"
          aria-label={`Correct ${label.toLowerCase()}`}
          title={`Correct ${label.toLowerCase()}`}
        >
          <Pencil size={11} strokeWidth={1.75} />
        </button>
      )}
    </span>
  );
}

export default function PinnedMasthead({ boat, subline }: PinnedMastheadProps) {
  const dateline = useMemo(() => nowStamp(), []);

  const tcc = boat.irc_tcc;
  const ratingLabel = tcc != null ? "IRC" : boat.orc_gph != null ? "ORC" : null;
  const ratingValue = tcc != null
    ? Number(tcc).toFixed(4)
    : boat.orc_gph != null
    ? Number(boat.orc_gph).toFixed(1)
    : null;
  const ratingUnit = tcc != null ? "TCC" : "GPH";

  return (
    <div className="sticky top-0 z-30">
      {/* Navy strip — wordmark + dateline */}
      <div className="bg-navy text-cream px-6 sm:px-12 h-[60px] sm:h-[72px] flex items-center justify-between border-b border-navy-light">
        <span className="brand-wordmark text-cream text-[15px] sm:text-base">
          Sail Ratings
        </span>
        <span className="data-mono text-[10px] uppercase tracking-[0.18em] text-cream/70 hidden sm:inline">
          The Bench · {dateline}
        </span>
        <span className="data-mono text-[10px] uppercase tracking-[0.18em] text-cream/70 sm:hidden">
          The Bench
        </span>
      </div>

      {/* Cream masthead row — boat name, TCC, metadata */}
      <div className="bg-cream border-b border-charcoal/15 px-6 sm:px-12 py-3.5 sm:py-4">
        <div className="flex items-baseline justify-between gap-4 flex-wrap">
          <div className="flex items-baseline gap-4 flex-wrap min-w-0">
            <h1
              className="heading-display text-charcoal uppercase tracking-[-0.015em] leading-none truncate"
              style={{ fontSize: "clamp(1.3rem, 2.4vw, 1.9rem)" }}
            >
              {boat.boat_name}
            </h1>
            {boat.sail_number && (
              <span className="data-mono text-[13px] text-charcoal/85 font-semibold">
                {boat.sail_number}
              </span>
            )}
            {ratingValue && (
              <span className="data-mono text-charcoal font-semibold text-[15px] sm:text-[17px]">
                <span className="text-brass mr-1.5">{ratingLabel}</span>
                {ratingValue}
                <span className="text-charcoal/60 ml-1 text-[11px] uppercase tracking-[0.12em] font-semibold">
                  {ratingUnit}
                </span>
              </span>
            )}
          </div>
          <div className="flex flex-wrap items-baseline gap-x-5 gap-y-1.5 text-[12px]">
            {boat.design && (
              <EditableField
                boatId={boat.id}
                label="Design"
                value={boat.design}
                fieldName="design_canonical"
              />
            )}
            <EditableField
              boatId={boat.id}
              label="Year"
              value={boat.year_built}
              fieldName="year_built"
            />
            <EditableField
              boatId={boat.id}
              label="Designer"
              value={boat.designer}
              fieldName="designer"
            />
            <EditableField
              boatId={boat.id}
              label="Builder"
              value={boat.builder}
              fieldName="builder"
            />
          </div>
        </div>
        {subline && (
          <div className="mt-2 data-mono text-[10px] uppercase tracking-[0.16em] text-charcoal/65 font-semibold">
            {subline}
          </div>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Lint the new file**

Run: `cd web && npm run lint -- src/components/PinnedMasthead.tsx`
Expected: no errors. (Warnings about unused imports are not allowed.)

- [ ] **Step 3: Commit**

```bash
cd /home/irc-data/code/sailratings
git add web/src/components/PinnedMasthead.tsx
git commit -m "feat(web): add PinnedMasthead component (sticky navy strip + masthead row)"
```

---

## Task 2: Create `StickyCheckoutRail` component

**Files:**
- Create: `web/src/components/StickyCheckoutRail.tsx`

**Background:** Persistent charcoal rail at the viewport bottom. Visible only after `visible === true` (parent controls — appears once §1 prose has finished streaming). Holds the primary checkout button, an optional sample-PDF link, the price, and a reassurance line. The component owns its own checkout-loading state via `isCheckingOut` and calls the parent's `onCheckout` callback. The price + currency comes from `detectCurrency()` (already in `web/src/lib/currency.ts`).

- [ ] **Step 1: Write the component file**

Create `web/src/components/StickyCheckoutRail.tsx`:

```tsx
"use client";

import { useMemo, useState } from "react";
import { ArrowRight, Loader2 } from "lucide-react";
import { detectCurrency } from "@/lib/currency";

interface StickyCheckoutRailProps {
  /** When false, the rail is hidden — used to delay reveal until §1 has streamed. */
  visible: boolean;
  /** Boat name interpolated into the primary button copy. */
  boatName: string;
  /** Async handler invoked when the primary button is clicked. Should redirect to Stripe. */
  onCheckout: () => Promise<void>;
  /** If set, the secondary "See a sample report" link points here. Hidden if unset. */
  samplePdfUrl?: string;
  /** Reassurance line shown to the right of the rail. Override the default if needed. */
  reassurance?: string;
}

export default function StickyCheckoutRail({
  visible,
  boatName,
  onCheckout,
  samplePdfUrl,
  reassurance = "PDF delivered the moment payment clears · One certificate, one report",
}: StickyCheckoutRailProps) {
  const [isCheckingOut, setIsCheckingOut] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const currency = useMemo(() => detectCurrency(), []);

  const handle = async () => {
    setIsCheckingOut(true);
    setError(null);
    try {
      await onCheckout();
    } catch {
      setError("Something went wrong. Please try again.");
      setIsCheckingOut(false);
    }
  };

  if (!visible) return null;

  return (
    <div
      className="fixed bottom-0 left-0 right-0 z-40 bg-charcoal text-cream border-t border-charcoal-light shadow-[0_-12px_30px_-12px_rgba(0,0,0,0.55)] animate-in"
      role="region"
      aria-label="Checkout"
    >
      <div className="max-w-6xl mx-auto px-6 sm:px-10 py-4 flex flex-col sm:flex-row items-stretch sm:items-center gap-3 sm:gap-6">
        <div className="flex-1 min-w-0 flex flex-col sm:flex-row sm:items-baseline gap-2 sm:gap-5">
          <p className="body-text text-cream text-[14px] sm:text-[15px] leading-snug truncate">
            Full file for <span className="font-semibold">{boatName}</span> — eight sections, ranked recommendations.
          </p>
          <span className="data-mono text-cream/55 text-[11px] uppercase tracking-[0.14em] hidden sm:inline">
            {reassurance}
          </span>
        </div>
        <div className="flex items-center gap-3 flex-shrink-0">
          {samplePdfUrl && (
            <a
              href={samplePdfUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center text-cream/75 hover:text-cream text-[12px] uppercase tracking-[0.1em] font-body font-semibold border-b border-cream/30 hover:border-cream pb-px transition-colors"
            >
              Sample report
            </a>
          )}
          <button
            onClick={() => void handle()}
            disabled={isCheckingOut}
            className="group inline-flex items-center gap-3 bg-brass text-navy px-5 sm:px-6 py-3 text-[13px] font-body font-semibold uppercase tracking-[0.08em] hover:bg-cream active:translate-y-px transition-all disabled:opacity-60"
          >
            {isCheckingOut ? (
              <>
                <Loader2 size={14} className="animate-spin" />
                Opening checkout…
              </>
            ) : (
              <>
                Send me the file — {currency.display}
                <ArrowRight size={14} strokeWidth={2.5} className="transition-transform group-hover:translate-x-1" />
              </>
            )}
          </button>
        </div>
      </div>
      {error && (
        <div className="max-w-6xl mx-auto px-6 sm:px-10 pb-3 text-right">
          <span className="body-text text-[12px] text-brass">{error}</span>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Lint**

Run: `cd web && npm run lint -- src/components/StickyCheckoutRail.tsx`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
cd /home/irc-data/code/sailratings
git add web/src/components/StickyCheckoutRail.tsx
git commit -m "feat(web): add StickyCheckoutRail (persistent charcoal CTA bar)"
```

---

## Task 3: Create `SealedSectionGrid` component

**Files:**
- Create: `web/src/components/SealedSectionGrid.tsx`

**Background:** Two-column grid of brass-stamped sealed section cards. Replaces the `RedactedSketch` blocks with a cleaner, less broken-looking pattern: each card is a brass-bordered cream tile with the section number, title, and dek, plus a brass band at the bottom reading `Sealed · §N of 8`. The seven sections (§2–§8) are the same data structure that lived in `TeaserAnalysis.tsx` — pull it out so the section list is owned here.

- [ ] **Step 1: Write the component file**

Create `web/src/components/SealedSectionGrid.tsx`:

```tsx
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

export default function SealedSectionGrid() {
  return (
    <section className="px-6 sm:px-12 py-10 sm:py-12" aria-label="Sealed sections">
      <div className="flex items-baseline justify-between mb-6 border-b border-charcoal/15 pb-4">
        <div className="data-mono text-[10px] uppercase tracking-[0.18em] text-charcoal/70 font-semibold">
          Seven sections drafted · sealed pending order
        </div>
        <div className="data-mono text-[10px] uppercase tracking-[0.18em] text-brass hidden sm:inline">
          §2 — §8
        </div>
      </div>
      <ol className="grid grid-cols-1 md:grid-cols-2 gap-4 sm:gap-5">
        {SEALED_SECTIONS.map((sec) => (
          <li
            key={sec.num}
            className="bg-cream border border-brass/30 hover:border-brass/60 transition-colors flex flex-col"
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
```

- [ ] **Step 2: Lint**

Run: `cd web && npm run lint -- src/components/SealedSectionGrid.tsx`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
cd /home/irc-data/code/sailratings
git add web/src/components/SealedSectionGrid.tsx
git commit -m "feat(web): add SealedSectionGrid (brass-stamped 2-col sealed cards)"
```

---

## Task 4: Refactor `TeaserAnalysis` to the two-column bench layout

**Files:**
- Modify: `web/src/components/TeaserAnalysis.tsx` (rewrite — file is being substantially restructured)

**Background:** This is the largest change. The current file does too much: masthead, working-steps reveal, §1 prose, redacted sketches for §2–§8, inline navy CTA, sticky bottom-right pill. Strip it down to: stream state + two-column bench (working-log left / prose right), mount `PinnedMasthead` + `SealedSectionGrid` + `StickyCheckoutRail`. The masthead now lives in `PinnedMasthead` so this file no longer renders boat metadata. The `RedactedSketch` helper and the `SECTIONS` constant are deleted (the section list moved into `SealedSectionGrid`).

Working-log behavior must obey `[[feedback-streaming-pacing]]`: no count, no enumeration. Steps appear with a `Loader2`, tick to `Check` on completion, hold visible afterward. Add a small `mm:ss.fff` timestamp on the right edge per the spec's "audit trail" framing.

- [ ] **Step 1: Replace the file contents**

Overwrite `web/src/components/TeaserAnalysis.tsx`:

```tsx
"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Check, Loader2 } from "lucide-react";
import {
  streamInsights,
  createCheckoutSession,
  type BoatDetail,
  type SSEStep,
} from "@/lib/api";
import { detectCurrency } from "@/lib/currency";
import PinnedMasthead from "@/components/PinnedMasthead";
import SealedSectionGrid from "@/components/SealedSectionGrid";
import StickyCheckoutRail from "@/components/StickyCheckoutRail";

interface TeaserAnalysisProps {
  boat: BoatDetail;
  searchQuery?: string;
  onComplete?: (text: string) => void;
  /** Optional sample PDF URL — passed through to the sticky CTA rail. */
  samplePdfUrl?: string;
}

interface StepEntry extends SSEStep {
  state: "active" | "done";
  /** Stamp captured when this step first appeared, formatted `mm:ss.fff` relative to stream start. */
  stamp: string;
}

function formatStamp(ms: number): string {
  const totalMs = Math.max(0, ms);
  const mm = String(Math.floor(totalMs / 60000)).padStart(2, "0");
  const ss = String(Math.floor((totalMs % 60000) / 1000)).padStart(2, "0");
  const fff = String(totalMs % 1000).padStart(3, "0");
  return `${mm}:${ss}.${fff}`;
}

export default function TeaserAnalysis({
  boat,
  searchQuery,
  onComplete,
  samplePdfUrl,
}: TeaserAnalysisProps) {
  const [steps, setSteps] = useState<StepEntry[]>([]);
  const [text, setText] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const [isDone, setIsDone] = useState(false);
  const [streamStarted, setStreamStarted] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const startedRef = useRef(false);
  const textRef = useRef("");
  const startTimeRef = useRef<number>(0);
  const currency = useMemo(() => detectCurrency(), []);

  useEffect(() => {
    if (startedRef.current) return;
    startedRef.current = true;

    let cancelled = false;

    async function run() {
      setIsStreaming(true);
      setError(null);
      setText("");
      textRef.current = "";
      setSteps([]);
      setStreamStarted(false);
      startTimeRef.current = Date.now();
      const { track } = await import("@/lib/posthog");
      track("teaser_started", { boat_id: boat.id });

      try {
        const stream = streamInsights(boat.id, "free");
        for await (const event of stream) {
          if (cancelled) break;

          if (event.type === "step") {
            const stepData = event.data as SSEStep;
            const stamp = formatStamp(Date.now() - startTimeRef.current);
            setSteps((prev) => {
              const next: StepEntry[] = prev.map((s) =>
                s.state === "active" ? { ...s, state: "done" } : s,
              );
              next.push({ ...stepData, state: "active", stamp });
              return next;
            });
          } else if (event.type === "text") {
            if (!streamStarted) {
              setStreamStarted(true);
              setSteps((prev) =>
                prev.map((s) => (s.state === "active" ? { ...s, state: "done" } : s)),
              );
            }
            textRef.current += event.data as string;
            setText(textRef.current);
          } else if (event.type === "done") {
            break;
          } else if (event.type === "error") {
            setError((event.data as string) || "Analysis failed.");
            break;
          }
        }
      } catch {
        if (!cancelled) setError("Failed to load analysis. Please try again.");
      } finally {
        if (!cancelled) {
          setIsStreaming(false);
          setIsDone(true);
          setSteps((prev) =>
            prev.map((s) => (s.state === "active" ? { ...s, state: "done" } : s)),
          );
          const ms = Date.now() - startTimeRef.current;
          track("teaser_completed", {
            boat_id: boat.id,
            duration_ms: ms,
            char_count: textRef.current.length,
            had_error: !!error,
          });
          if (textRef.current && onComplete) onComplete(textRef.current);
        }
      }
    }

    run();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [boat.id]);

  const handleCheckout = async () => {
    const { track } = await import("@/lib/posthog");
    track("buy_clicked", {
      boat_id: boat.id,
      boat_name: boat.boat_name,
      currency: currency.code,
      search_query: searchQuery,
      placement: "rail",
    });
    const { checkout_url, order_token } = await createCheckoutSession({
      boat_id: boat.id,
      boat_name: boat.boat_name,
      currency: currency.code,
      search_query: searchQuery,
      teaser_text: textRef.current,
    });
    track("checkout_redirect", { boat_id: boat.id, order_token, currency: currency.code });
    window.location.href = checkout_url;
  };

  if (error && !text) {
    return (
      <div className="w-full max-w-3xl mx-auto px-6 py-12">
        <p className="body-text text-brass">{error}</p>
      </div>
    );
  }

  return (
    <>
      <PinnedMasthead boat={boat} />

      <article id="bench" className="w-full max-w-5xl mx-auto bg-cream/90 text-charcoal pb-24">
        {/* Two-column bench: working log (left) + §1 prose (right). On mobile, stacks. */}
        <section className="grid grid-cols-1 md:grid-cols-[minmax(220px,1fr)_2.5fr] gap-0 border-b border-charcoal/15">
          {/* Working log */}
          <aside className="bg-charcoal/[0.03] px-6 sm:px-8 py-6 md:py-10 border-b md:border-b-0 md:border-r border-charcoal/15">
            <div className="data-mono text-[10px] uppercase tracking-[0.18em] text-brass font-semibold mb-4">
              Working
            </div>
            {steps.length === 0 ? (
              <div className="flex items-center gap-2 data-mono text-[11px] uppercase tracking-[0.14em] text-charcoal/65">
                <Loader2 size={11} className="animate-spin text-brass" />
                Opening the registry…
              </div>
            ) : (
              <ol className="space-y-2">
                {steps.map((s, i) => (
                  <li
                    key={i}
                    className="flex items-baseline gap-2 data-mono text-[11px] uppercase tracking-[0.12em] text-charcoal/80"
                  >
                    <span className="flex-shrink-0 w-3">
                      {s.state === "done" ? (
                        <Check size={11} strokeWidth={2.5} className="text-brass" />
                      ) : (
                        <Loader2 size={10} className="animate-spin text-brass" />
                      )}
                    </span>
                    <span className="flex-1 min-w-0">
                      <span className="block truncate">{s.label}</span>
                      {s.detail && (
                        <span className="block text-charcoal/55 normal-case tracking-normal text-[11px] mt-0.5">
                          {s.detail}
                        </span>
                      )}
                    </span>
                    <span className="data-mono text-[9px] text-charcoal/40 ml-1 flex-shrink-0">
                      {s.stamp}
                    </span>
                  </li>
                ))}
              </ol>
            )}
          </aside>

          {/* §1 prose */}
          <div className="px-6 sm:px-10 py-8 sm:py-10">
            <h2 className="flex items-baseline gap-4 mb-5">
              <span className="data-mono text-brass text-[12px] font-semibold tracking-[0.1em] flex-shrink-0">
                §01
              </span>
              <span className="heading-display text-charcoal text-xl sm:text-2xl">
                Where she sits
              </span>
            </h2>
            {text ? (
              <div
                className="body-text text-charcoal text-[16px] sm:text-[17px] leading-[1.62] whitespace-pre-wrap"
                style={{ maxWidth: "62ch" }}
              >
                {text}
                {isStreaming && streamStarted && (
                  <span className="inline-block w-0.5 h-5 bg-brass ml-0.5 align-text-bottom streaming-pulse" />
                )}
              </div>
            ) : (
              !streamStarted && (
                <div className="body-text text-charcoal/40 italic text-[15px]">Drafting…</div>
              )
            )}
          </div>
        </section>

        {/* Brass hairline rule */}
        {isDone && <div className="mx-6 sm:mx-12 h-px bg-brass/40" />}

        {/* Sealed sections grid (§2 — §8) */}
        {isDone && <SealedSectionGrid />}
      </article>

      {/* Persistent CTA rail — appears only once §1 has finished streaming */}
      <StickyCheckoutRail
        visible={isDone}
        boatName={boat.boat_name}
        onCheckout={handleCheckout}
        samplePdfUrl={samplePdfUrl}
      />
    </>
  );
}
```

- [ ] **Step 2: Lint**

Run: `cd web && npm run lint -- src/components/TeaserAnalysis.tsx`
Expected: no errors. Common pitfalls: unused `useCallback` imports, unused old `MastheadField` if accidentally kept.

- [ ] **Step 3: Build (catches type errors)**

Run: `cd web && ENVIRONMENT=dev npm run build`
Expected: build succeeds. If it fails on missing imports from `@/lib/api`, the types `BoatDetail`, `SSEStep`, `streamInsights`, `createCheckoutSession` all already exist in the current file's imports — verify they're still present.

- [ ] **Step 4: Commit**

```bash
cd /home/irc-data/code/sailratings
git add web/src/components/TeaserAnalysis.tsx
git commit -m "refactor(web): rewrite TeaserAnalysis as two-column bench with new chrome

- Mount PinnedMasthead at top (was inline masthead)
- Two-column layout: working-log left, §1 prose right
- Sealed sections delegated to SealedSectionGrid (removes RedactedSketch)
- CTA delegated to StickyCheckoutRail (removes inline navy block + sticky pill)
- Working steps gain mm:ss.fff timestamps; no enumeration"
```

---

## Task 5: Update `page.tsx` — remove `scrollIntoView`, render new structure

**Files:**
- Modify: `web/src/app/page.tsx`

**Background:** The auto-scroll-on-select effect (lines 48–54) is the source of the "screen moves down funny" jolt. With the pinned masthead taking the top of the viewport, no scroll is needed at all — the user stays where they are and the bench unfolds below. Also: simplify the loading-state JSX (the spinner + "Pulling Ariadne's file…" can stay but doesn't need its own section wrapper now that nothing else sits between the hero and the bench).

- [ ] **Step 1: Replace the file**

Overwrite `web/src/app/page.tsx`:

```tsx
"use client";

import { useState, useEffect, useCallback } from "react";
import { Loader2 } from "lucide-react";
import Hero from "@/components/Hero";
import TeaserAnalysis from "@/components/TeaserAnalysis";
import { getBoat, type SearchResult, type BoatDetail } from "@/lib/api";

export default function Home() {
  const [selectedBoat, setSelectedBoat] = useState<SearchResult | null>(null);
  const [boatDetail, setBoatDetail] = useState<BoatDetail | null>(null);
  const [boatError, setBoatError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [, setTeaserText] = useState("");

  const handleBoatSelected = (boat: SearchResult) => {
    setSelectedBoat(boat);
    setBoatDetail(null);
    setBoatError(null);
    setTeaserText("");
    setSearchQuery(boat.boat_name);
  };

  const handleTeaserComplete = useCallback((text: string) => {
    setTeaserText(text);
  }, []);

  useEffect(() => {
    if (!selectedBoat) return;
    let cancelled = false;
    setBoatDetail(null);
    setBoatError(null);
    (async () => {
      try {
        const data = await getBoat(selectedBoat.id);
        if (!cancelled) setBoatDetail(data);
      } catch {
        if (!cancelled) setBoatError("Failed to load boat details.");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [selectedBoat]);

  return (
    <main className="min-h-screen bg-cream">
      {!selectedBoat && <Hero onBoatSelected={handleBoatSelected} />}

      {selectedBoat && (
        <>
          {!boatDetail && !boatError && (
            <div className="max-w-3xl mx-auto px-6 py-20 flex items-center gap-3">
              <Loader2 size={16} className="animate-spin text-brass" />
              <span className="body-text text-charcoal/60 text-sm">
                Pulling {selectedBoat.boat_name}&rsquo;s file…
              </span>
            </div>
          )}
          {boatError && (
            <div className="max-w-3xl mx-auto px-6 py-20">
              <p className="body-text text-brass text-sm">{boatError}</p>
            </div>
          )}
          {boatDetail && (
            <TeaserAnalysis
              key={`teaser-${selectedBoat.id}`}
              boat={boatDetail}
              searchQuery={searchQuery}
              onComplete={handleTeaserComplete}
            />
          )}
        </>
      )}

      {!selectedBoat && (
        <footer className="border-t border-border px-6 py-8 bg-cream">
          <div className="max-w-2xl mx-auto flex flex-col sm:flex-row items-center justify-between gap-3 text-[11px] text-muted">
            <span className="brand-wordmark text-muted/40">Sail Ratings</span>
            <span className="body-text text-center">
              Rating data sourced from public certificates. Not affiliated with the RORC Rating Office or ORC.
            </span>
          </div>
        </footer>
      )}
    </main>
  );
}
```

Note: the Hero (with search) is hidden once a boat is selected so PinnedMasthead can take the top of the viewport without overlap. The footer is hidden during the bench so the sticky CTA rail doesn't sit on top of it.

- [ ] **Step 2: Lint + build**

Run: `cd web && npm run lint && ENVIRONMENT=dev npm run build`
Expected: both succeed. Unused-imports cleanup: `useRef` was removed in this rewrite, which was previously used for `resultsRef` — confirm nothing else references it.

- [ ] **Step 3: Commit**

```bash
cd /home/irc-data/code/sailratings
git add web/src/app/page.tsx
git commit -m "fix(web): remove scrollIntoView on boat select; hide hero during bench

The scrollIntoView fired before the bench content existed, leaving the
user looking at a spinner above the fold. With the pinned masthead
taking the top of the viewport, no programmatic scroll is needed."
```

---

## Task 6: Update `ReportView` — pinned masthead + table-first reveal

**Files:**
- Modify: `web/src/components/ReportView.tsx`
- Modify: `web/src/app/report/[token]/page.tsx`

**Background:** The buyer paid for the answer. Currently `ReportView` shows the boat header, then progressively reveals the prose paragraph-by-paragraph (280ms), then the recommendation table, RAI, and rivals appear once prose is done. Invert: show the masthead (same `PinnedMasthead` as the bench — zero visual jump from checkout), then a 600ms "File compiled. Opening…" subline, then the recommendations table drawn first, then the prose backfills, then RAI + rivals.

The existing `useStreamedMarkdown` hook is preserved — it still paces the prose. We just gate it behind the table appearing first.

- [ ] **Step 1: Rewrite `ReportView.tsx`**

Overwrite `web/src/components/ReportView.tsx`:

```tsx
"use client";

import { useEffect, useState } from "react";
import { Download, Trophy, Target, Swords } from "lucide-react";
import { getReportPdfUrl, type ReportData } from "@/lib/api";
import PinnedMasthead from "@/components/PinnedMasthead";

interface ReportViewProps {
  report: ReportData;
  token: string;
}

function useStreamedMarkdown(fullText: string | undefined, msPerParagraph = 280, startAfterMs = 0) {
  const [revealed, setRevealed] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const [isComplete, setIsComplete] = useState(false);

  useEffect(() => {
    if (!fullText) {
      setRevealed("");
      setIsStreaming(false);
      setIsComplete(false);
      return;
    }

    const paragraphs = fullText.split(/\n\n+/).filter((p) => p.trim());
    if (paragraphs.length === 0) {
      setRevealed(fullText);
      setIsComplete(true);
      return;
    }

    let cancelled = false;
    let i = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    setRevealed("");
    setIsStreaming(true);
    setIsComplete(false);

    const tick = () => {
      if (cancelled) return;
      i += 1;
      setRevealed(paragraphs.slice(0, i).join("\n\n"));
      if (i >= paragraphs.length) {
        setIsStreaming(false);
        setIsComplete(true);
        return;
      }
      timer = setTimeout(tick, msPerParagraph);
    };

    timer = setTimeout(tick, startAfterMs);

    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [fullText, msPerParagraph, startAfterMs]);

  return { revealed, isStreaming, isComplete };
}

export default function ReportView({ report, token }: ReportViewProps) {
  const { boat, report_markdown, recommendations, rai, rivals } = report;
  const [phase, setPhase] = useState<"compiling" | "table" | "prose">("compiling");

  // 600ms "File compiled. Opening…" beat, then draw the recommendations table.
  useEffect(() => {
    const t1 = setTimeout(() => setPhase("table"), 600);
    // Prose begins backfilling shortly after the table is visible.
    const t2 = setTimeout(() => setPhase("prose"), 1400);
    return () => {
      clearTimeout(t1);
      clearTimeout(t2);
    };
  }, []);

  const tableReady = phase === "table" || phase === "prose";
  const proseReady = phase === "prose";

  const { revealed, isStreaming, isComplete: proseDone } = useStreamedMarkdown(
    proseReady && report_markdown ? report_markdown : undefined,
    280,
    0,
  );

  return (
    <>
      {boat && (
        <PinnedMasthead
          boat={boat}
          subline={
            phase === "compiling" ? (
              <span className="text-brass">File compiled. Opening…</span>
            ) : null
          }
        />
      )}

      <div className="max-w-3xl mx-auto px-6 py-10 space-y-12">
        {/* Download PDF chip */}
        {boat && (
          <div className="flex items-center justify-end">
            <a
              href={getReportPdfUrl(token)}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-2 text-brass hover:text-brass-dark transition-colors font-body text-sm font-medium"
            >
              <Download size={16} strokeWidth={1.5} />
              Download PDF
            </a>
          </div>
        )}

        {/* Recommendations table — drawn FIRST, before prose */}
        {tableReady && recommendations && recommendations.length > 0 && (
          <section className="border border-border bg-white animate-in">
            <div className="border-b border-border-light px-8 py-5 flex items-center gap-3">
              <Target size={18} strokeWidth={1.5} className="text-brass" />
              <h2 className="heading-display text-xl text-charcoal">
                Optimisation Recommendations
              </h2>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-left">
                <thead>
                  <tr className="border-b border-border-light text-xs uppercase tracking-wider text-muted">
                    <th className="px-6 py-3 font-medium">#</th>
                    <th className="px-6 py-3 font-medium">Field</th>
                    <th className="px-6 py-3 font-medium">Category</th>
                    <th className="px-6 py-3 font-medium text-right">Current</th>
                    <th className="px-6 py-3 font-medium text-right">Mean</th>
                    <th className="px-6 py-3 font-medium text-right">TCC Delta</th>
                    <th className="px-6 py-3 font-medium">Feasibility</th>
                  </tr>
                </thead>
                <tbody>
                  {recommendations.map((rec, i) => (
                    <tr
                      key={i}
                      className={`border-b border-border-light last:border-b-0 ${
                        i % 2 === 0 ? "bg-white" : "bg-cream/50"
                      }`}
                    >
                      <td className="px-6 py-4">
                        <span className="data-mono text-sm text-brass font-semibold">{rec.rank}</span>
                      </td>
                      <td className="px-6 py-4">
                        <span className="body-text text-sm text-charcoal font-medium">{rec.field}</span>
                      </td>
                      <td className="px-6 py-4">
                        <span className="body-text text-xs text-muted">{rec.category}</span>
                      </td>
                      <td className="px-6 py-4 text-right">
                        <span className="data-mono text-sm text-charcoal">{formatValue(rec.current_value)}</span>
                      </td>
                      <td className="px-6 py-4 text-right">
                        <span className="data-mono text-sm text-muted">{formatValue(rec.mean_value)}</span>
                      </td>
                      <td className="px-6 py-4 text-right">
                        <span
                          className={`data-mono text-sm font-semibold ${
                            rec.tcc_delta < 0 ? "text-navy" : "text-brass"
                          }`}
                        >
                          {rec.tcc_delta > 0 ? "+" : ""}
                          {Number(rec.tcc_delta).toFixed(4)}
                        </span>
                      </td>
                      <td className="px-6 py-4">
                        <span className="body-text text-xs text-muted">{rec.feasibility}</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="border-t border-border-light px-8 py-6 space-y-4">
              {recommendations.map((rec, i) => (
                <div key={i}>
                  <div className="flex items-baseline gap-2 mb-1">
                    <span className="data-mono text-xs text-brass font-semibold">{rec.rank}.</span>
                    <span className="body-text text-sm text-charcoal font-medium">{rec.field}</span>
                    {rec.evidence_strength && (
                      <span className="data-mono text-[10px] text-muted uppercase">{rec.evidence_strength}</span>
                    )}
                  </div>
                  <p className="body-text text-sm text-charcoal-light pl-6">{rec.explanation}</p>
                </div>
              ))}
            </div>
          </section>
        )}

        {/* Analysis prose — backfills AFTER the table is on screen */}
        {proseReady && report_markdown && (
          <section className="border border-border bg-white px-8 py-8 sm:px-12 sm:py-10 animate-in">
            <div
              className="prose-report"
              dangerouslySetInnerHTML={{ __html: markdownToHtml(revealed) }}
            />
            {isStreaming && (
              <span
                className="inline-block w-0.5 h-5 bg-brass align-text-bottom streaming-pulse ml-0.5"
                aria-hidden="true"
              />
            )}
          </section>
        )}

        {/* RAI card */}
        {proseDone && rai && (
          <section className="border border-border bg-white animate-in">
            <div className="border-b border-border-light px-8 py-5 flex items-center gap-3">
              <Trophy size={18} strokeWidth={1.5} className="text-brass" />
              <h2 className="heading-display text-xl text-charcoal">Racing Performance Index</h2>
            </div>
            <div className="px-8 py-8 sm:px-12">
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-6 mb-6">
                <StatCard label="RAI Score" value={rai.rai_score.toFixed(2)} />
                <StatCard label="Races" value={String(rai.n_races)} />
                <StatCard label="Wins" value={String(rai.wins)} />
                <StatCard label="Podiums" value={String(rai.podiums)} />
              </div>
              <div className="flex items-center gap-2 mb-4">
                <span className="data-mono text-xs text-muted">
                  95% CI: {rai.ci_low.toFixed(2)} &ndash; {rai.ci_high.toFixed(2)}
                </span>
              </div>
              {rai.interpretation && (
                <p className="body-text text-sm text-charcoal-light">{rai.interpretation}</p>
              )}
            </div>
          </section>
        )}

        {/* Rivals table */}
        {proseDone && rivals && rivals.length > 0 && (
          <section className="border border-border bg-white animate-in">
            <div className="border-b border-border-light px-8 py-5 flex items-center gap-3">
              <Swords size={18} strokeWidth={1.5} className="text-brass" />
              <h2 className="heading-display text-xl text-charcoal">Head-to-Head Rivals</h2>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-left">
                <thead>
                  <tr className="border-b border-border-light text-xs uppercase tracking-wider text-muted">
                    <th className="px-6 py-3 font-medium">Boat</th>
                    <th className="px-6 py-3 font-medium">Sail #</th>
                    <th className="px-6 py-3 font-medium text-right">W</th>
                    <th className="px-6 py-3 font-medium text-right">L</th>
                    <th className="px-6 py-3 font-medium text-right">Win %</th>
                    <th className="px-6 py-3 font-medium text-right">Events</th>
                  </tr>
                </thead>
                <tbody>
                  {rivals.map((rival, i) => (
                    <tr
                      key={i}
                      className={`border-b border-border-light last:border-b-0 ${
                        i % 2 === 0 ? "bg-white" : "bg-cream/50"
                      }`}
                    >
                      <td className="px-6 py-4">
                        <span className="body-text text-sm text-charcoal font-medium">{rival.boat_name}</span>
                      </td>
                      <td className="px-6 py-4">
                        <span className="data-mono text-xs text-muted">{rival.sail_number}</span>
                      </td>
                      <td className="px-6 py-4 text-right">
                        <span className="data-mono text-sm text-navy">{rival.wins}</span>
                      </td>
                      <td className="px-6 py-4 text-right">
                        <span className="data-mono text-sm text-charcoal">{rival.losses}</span>
                      </td>
                      <td className="px-6 py-4 text-right">
                        <span className="data-mono text-sm text-charcoal font-medium">
                          {(rival.win_rate * 100).toFixed(0)}%
                        </span>
                      </td>
                      <td className="px-6 py-4 text-right">
                        <span className="data-mono text-sm text-muted">{rival.events}</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        )}
      </div>
    </>
  );
}

function StatCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="text-center">
      <div className="data-mono text-2xl text-charcoal font-semibold">{value}</div>
      <div className="text-xs text-muted uppercase tracking-wider mt-1">{label}</div>
    </div>
  );
}

function formatValue(val: number | string): string {
  if (typeof val === "number") return val.toFixed(3);
  return String(val);
}

function markdownToHtml(md: string): string {
  return md
    .replace(/^### (.+)$/gm, '<h3 class="heading-display text-lg text-charcoal mt-8 mb-3">$1</h3>')
    .replace(/^## (.+)$/gm, '<h2 class="heading-display text-xl text-charcoal mt-10 mb-4">$1</h2>')
    .replace(/^# (.+)$/gm, '<h1 class="heading-display text-2xl text-charcoal mt-10 mb-4">$1</h1>')
    .replace(/\*\*(.+?)\*\*/g, '<strong class="font-semibold text-charcoal">$1</strong>')
    .replace(/\*(.+?)\*/g, "<em>$1</em>")
    .replace(/\n\n/g, '</p><p class="body-text text-charcoal-light text-base leading-relaxed mb-4">')
    .replace(/^/, '<p class="body-text text-charcoal-light text-base leading-relaxed mb-4">')
    .replace(/$/, "</p>");
}
```

Note: the old report view used `text-ink` and `text-ink-light` color tokens which are not in the Tailwind config. They're replaced with `text-charcoal` and `text-charcoal-light` (which exist). If those legacy tokens were resolving via some CSS variable, they'll need re-checking visually in Task 9 — but the tailwind config audit in Task 1 setup shows no `ink` token defined, so this is a cleanup.

- [ ] **Step 2: Simplify `report/[token]/page.tsx`**

`ReportView` now owns the masthead, so the page's `<nav>` and footer-with-wordmark become redundant. Overwrite `web/src/app/report/[token]/page.tsx`:

```tsx
"use client";

import { useState, useEffect, useRef, use } from "react";
import { getReport, type ReportData } from "@/lib/api";
import ReportView from "@/components/ReportView";
import ReportSurvey from "@/components/ReportSurvey";
import Link from "next/link";

export default function ReportPage({
  params,
}: {
  params: Promise<{ token: string }>;
}) {
  const { token } = use(params);
  const [report, setReport] = useState<ReportData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval>>(undefined);

  useEffect(() => {
    let cancelled = false;

    async function fetchReport() {
      try {
        const data = await getReport(token);
        if (cancelled) return;
        setReport(data);
        if (data.status === "generated" || data.status === "ready" || data.status === "error") {
          if (pollRef.current) clearInterval(pollRef.current);
        }
      } catch {
        if (!cancelled) {
          setError("Failed to load your report. Please try again later.");
          if (pollRef.current) clearInterval(pollRef.current);
        }
      }
    }

    fetchReport();
    pollRef.current = setInterval(fetchReport, 3000);

    return () => {
      cancelled = true;
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [token]);

  if (error) {
    return (
      <main className="min-h-screen bg-cream flex items-center justify-center px-6">
        <div className="max-w-md text-center">
          <h1 className="heading-display text-3xl text-charcoal mb-4">Something went wrong</h1>
          <p className="body-text text-muted mb-8">{error}</p>
          <Link
            href="/"
            className="inline-block bg-brass text-white px-6 py-3 font-body font-medium hover:bg-brass-dark transition-colors"
            style={{ borderRadius: "1px" }}
          >
            Back to Home
          </Link>
        </div>
      </main>
    );
  }

  if (!report || report.status === "pending" || report.status === "paid") {
    return (
      <main className="min-h-screen bg-cream flex items-center justify-center px-6">
        <div className="max-w-md text-center">
          <div className="mb-8">
            <span className="brand-wordmark text-sm text-navy/40">Sail Ratings</span>
          </div>
          <div className="flex justify-center mb-6">
            <div
              className="w-8 h-8 border-2 border-border border-t-brass animate-spin"
              style={{ borderRadius: "50%" }}
            />
          </div>
          <h1 className="heading-display text-2xl text-charcoal mb-3">Preparing Your Report</h1>
          <p className="body-text text-muted text-base">
            {report?.status === "paid"
              ? "Payment confirmed. Generating your analysis…"
              : "Processing your payment…"}
          </p>
        </div>
      </main>
    );
  }

  if (report.status === "error") {
    return (
      <main className="min-h-screen bg-cream flex items-center justify-center px-6">
        <div className="max-w-md text-center">
          <h1 className="heading-display text-3xl text-charcoal mb-4">Report Error</h1>
          <p className="body-text text-muted mb-8">
            There was a problem generating your report. Please contact us for assistance.
          </p>
          <Link
            href="/"
            className="inline-block bg-brass text-white px-6 py-3 font-body font-medium hover:bg-brass-dark transition-colors"
            style={{ borderRadius: "1px" }}
          >
            Back to Home
          </Link>
        </div>
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-cream">
      <ReportView report={report} token={token} />
      <ReportSurvey orderToken={token} />
      <footer className="border-t border-border-light px-6 py-10">
        <div className="max-w-3xl mx-auto flex flex-col sm:flex-row items-center justify-between gap-4 text-xs text-muted">
          <span className="brand-wordmark text-xs text-muted/60">Sail Ratings</span>
          <span className="body-text text-center">
            IRC rating data sourced from public certificates. Not affiliated with the RORC Rating Office.
          </span>
        </div>
      </footer>
    </main>
  );
}
```

- [ ] **Step 3: Lint + build**

Run: `cd web && npm run lint && ENVIRONMENT=dev npm run build`
Expected: both succeed.

- [ ] **Step 4: Commit**

```bash
cd /home/irc-data/code/sailratings
git add web/src/components/ReportView.tsx web/src/app/report/[token]/page.tsx
git commit -m "feat(web): pinned masthead + table-first reveal on report page

- ReportView mounts PinnedMasthead at top (zero visual jump from checkout)
- 600ms 'File compiled. Opening…' beat in the masthead subline
- Recommendations table draws first; prose backfills after; RAI + rivals last
- Drop redundant nav header on the report page wrapper
- Replace dead text-ink token usage with text-charcoal"
```

---

## Task 7: Delete dead `PurchaseCTA.tsx`

**Files:**
- Delete: `web/src/components/PurchaseCTA.tsx`

**Background:** `PurchaseCTA` is imported nowhere — `grep -rn "PurchaseCTA"` returned only its own file. The new flow uses `StickyCheckoutRail`. Remove it.

- [ ] **Step 1: Verify nothing imports it**

Run: `cd web && grep -rn "PurchaseCTA" src/`
Expected: empty output (or only the file's own declaration if grep matches it before deletion).

- [ ] **Step 2: Delete the file**

```bash
rm /home/irc-data/code/sailratings/web/src/components/PurchaseCTA.tsx
```

- [ ] **Step 3: Lint + build to confirm nothing referenced it**

Run: `cd web && npm run lint && ENVIRONMENT=dev npm run build`
Expected: both succeed.

- [ ] **Step 4: Commit**

```bash
cd /home/irc-data/code/sailratings
git add -A web/src/components/PurchaseCTA.tsx
git commit -m "chore(web): delete dead PurchaseCTA.tsx (replaced by StickyCheckoutRail)"
```

---

## Task 8: Add Playwright screenshot tests

**Files:**
- Create: `web/tests/screenshot-bench.mjs`
- Create: `web/tests/screenshot-report.mjs`
- Modify: `web/tests/screenshot-search.mjs`

**Background:** Per `[[feedback-testing]]` every visual claim must be verified with a Playwright screenshot. These three scripts cover: search-to-bench transition (no jolt), the bench at three beats (streaming, sealed-visible, scrolled), and the report payoff (compiling subline, table-first, prose backfilling). All scripts default to `https://dev.sailratings.com/` but accept a URL argv for local iteration against `http://localhost:4200` or `http://localhost:3000`.

The `screenshot-bench.mjs` script needs a real boat token to land on the bench. Use `sun fish` as the search query (already confirmed to return 8 results in the smoke test) and click the first dropdown result.

- [ ] **Step 1: Create `screenshot-bench.mjs`**

```javascript
// Capture the bench at three beats:
//   1) streaming in progress (working log mid-flight, prose just starting)
//   2) sealed sections visible (post-stream, scrolled to show §2–§8)
//   3) sticky CTA rail engaged (scrolled to bottom)
// Usage: node tests/screenshot-bench.mjs [base-url]
//        defaults to https://dev.sailratings.com/

import { chromium } from 'playwright';

const baseUrl = process.argv[2] || 'https://dev.sailratings.com/';
const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await ctx.newPage();

await page.goto(baseUrl, { waitUntil: 'networkidle', timeout: 30000 });
await page.waitForTimeout(600);

// Search and pick the first result
await page.fill('#main-search', 'sun fish');
await page.waitForTimeout(900);
// First dropdown result. Hero.tsx uses role="combobox" + a result list — click the first option.
const firstResult = page.locator('[role="option"], [data-result], li button').first();
await firstResult.click();

// Wait for the bench panel to mount (PinnedMasthead is sticky top-0)
await page.waitForSelector('#bench', { timeout: 15000 });
await page.waitForTimeout(2200); // give SSE working steps + prose start a chance

// Beat 1: streaming in progress
await page.screenshot({ path: '/tmp/bench-streaming.png', fullPage: false });

// Wait for streaming to finish + sealed sections to appear
await page.waitForFunction(
  () => document.querySelector('[aria-label="Sealed sections"]') !== null,
  { timeout: 60000 },
);
await page.waitForTimeout(800);

// Beat 2: sealed sections visible (in view)
await page.evaluate(() => {
  const el = document.querySelector('[aria-label="Sealed sections"]');
  if (el) el.scrollIntoView({ behavior: 'instant', block: 'start' });
});
await page.waitForTimeout(400);
await page.screenshot({ path: '/tmp/bench-sealed.png', fullPage: false });

// Beat 3: scrolled to bottom, sticky CTA rail engaged
await page.evaluate(() => window.scrollTo({ top: document.body.scrollHeight, behavior: 'instant' }));
await page.waitForTimeout(400);
await page.screenshot({ path: '/tmp/bench-cta.png', fullPage: false });

console.log('OK', baseUrl);
console.log('  /tmp/bench-streaming.png');
console.log('  /tmp/bench-sealed.png');
console.log('  /tmp/bench-cta.png');
await browser.close();
```

- [ ] **Step 2: Create `screenshot-report.mjs`**

This script requires a real `/report/[token]` URL because the report depends on an order token. Accept the token URL as argv. The test verifies the masthead is pinned and the table appears before the prose finishes (we screenshot at 700ms and 2500ms after load).

```javascript
// Capture the report-page payoff:
//   1) compiling beat (PinnedMasthead with "File compiled. Opening…" subline)
//   2) table-first (recommendations table visible, prose not yet started)
//   3) prose backfilling (prose visible, table still above)
// Usage: node tests/screenshot-report.mjs <full-report-url>
//   e.g. node tests/screenshot-report.mjs https://dev.sailratings.com/report/abc123

import { chromium } from 'playwright';

const url = process.argv[2];
if (!url) {
  console.error('Usage: node tests/screenshot-report.mjs <full-report-url>');
  process.exit(1);
}

const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await ctx.newPage();

await page.goto(url, { waitUntil: 'networkidle', timeout: 30000 });

// Beat 1: compiling moment (should land within 600ms of load)
await page.waitForTimeout(300);
await page.screenshot({ path: '/tmp/report-compiling.png', fullPage: false });

// Beat 2: table-first (around 900–1400ms after load — table mounted, prose not yet)
await page.waitForTimeout(700);
await page.screenshot({ path: '/tmp/report-table.png', fullPage: false });

// Beat 3: prose backfilling (around 2.5s — prose has started, table still on screen)
await page.waitForTimeout(1500);
await page.screenshot({ path: '/tmp/report-prose.png', fullPage: false });

console.log('OK', url);
console.log('  /tmp/report-compiling.png');
console.log('  /tmp/report-table.png');
console.log('  /tmp/report-prose.png');
await browser.close();
```

- [ ] **Step 3: Update `screenshot-search.mjs` to capture the jump-free transition**

Overwrite `web/tests/screenshot-search.mjs`:

```javascript
// Search bar: empty, typed, dropdown — plus the jump-free transition to the bench.
// Usage: node tests/screenshot-search.mjs [base-url]

import { chromium } from 'playwright';

const baseUrl = process.argv[2] || 'https://dev.sailratings.com/';
const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await ctx.newPage();

await page.goto(baseUrl, { waitUntil: 'networkidle', timeout: 30000 });
await page.waitForTimeout(800);

// Empty state
await page.screenshot({ path: '/tmp/search-empty.png', clip: { x: 360, y: 350, width: 720, height: 120 } });

// Typed state
await page.fill('#main-search', 'sun fish');
await page.waitForTimeout(700);
await page.screenshot({ path: '/tmp/search-typed.png', clip: { x: 360, y: 350, width: 720, height: 120 } });

// Dropdown
await page.waitForTimeout(400);
await page.screenshot({ path: '/tmp/search-dropdown.png', clip: { x: 360, y: 350, width: 720, height: 480 } });

// Jump-free transition: capture the viewport in the 250ms after click to confirm no scroll jolt.
const firstResult = page.locator('[role="option"], [data-result], li button').first();
const scrollBefore = await page.evaluate(() => window.scrollY);
await firstResult.click();
await page.waitForTimeout(250);
const scrollAfter = await page.evaluate(() => window.scrollY);
await page.screenshot({ path: '/tmp/search-after-click.png', fullPage: false });

console.log('OK', baseUrl);
console.log('  /tmp/search-empty.png');
console.log('  /tmp/search-typed.png');
console.log('  /tmp/search-dropdown.png');
console.log('  /tmp/search-after-click.png');
console.log(`  scrollY before=${scrollBefore} after=${scrollAfter} (delta=${scrollAfter - scrollBefore})`);
if (scrollAfter - scrollBefore > 4) {
  console.error('FAIL: scroll jolt detected');
  process.exit(2);
}
await browser.close();
```

- [ ] **Step 4: Commit the test scripts**

```bash
cd /home/irc-data/code/sailratings
git add web/tests/screenshot-bench.mjs web/tests/screenshot-report.mjs web/tests/screenshot-search.mjs
git commit -m "test(web): add bench + report screenshot tests; assert no scroll jolt on pick"
```

---

## Task 9: Build, screenshot, visually verify the full flow

**Files:** (verification only — no code changes unless screenshots reveal a problem)

**Background:** The user instruction `[[feedback-testing]]` is strict: every visual change must be Playwright-screenshotted and **the screenshot itself must be read** before claiming done. This task is the verification gate.

- [ ] **Step 1: Build production bundle and restart**

```bash
cd /home/irc-data/code/sailratings/web
ENVIRONMENT=dev npm run build
NEXT_PID=$(ss -tlnp 2>/dev/null | grep ':4200' | grep -oP 'pid=\K[0-9]+' | head -1)
if [ -n "$NEXT_PID" ]; then kill "$NEXT_PID"; sleep 1; fi
setsid nohup ./node_modules/.bin/next start -p 4200 \
  > /tmp/sailratings.log 2>&1 < /dev/null & disown
sleep 4
```

Then smoke-check:

```bash
curl -s -o /dev/null -w "front=%{http_code}\n" --max-time 5 https://dev.sailratings.com/
```
Expected: `front=200`.

- [ ] **Step 2: Run the search-transition test (asserts no scroll jolt)**

```bash
cd /home/irc-data/code/sailratings/web
node tests/screenshot-search.mjs https://dev.sailratings.com/
```
Expected: exit 0, `scrollY before=0 after=0 (delta=0)` printed.
If `FAIL: scroll jolt detected` appears, page.tsx still has the old effect — revisit Task 5.

- [ ] **Step 3: Run the bench three-beat capture**

```bash
node tests/screenshot-bench.mjs https://dev.sailratings.com/
```
Expected: three screenshots written. Read each one — use Claude's image-reading capability or open them:

```bash
ls -la /tmp/bench-*.png
```

Visual checklist for `/tmp/bench-streaming.png`:
- Sticky navy strip at top with "Sail Ratings" wordmark + "The Bench · <date>"
- Cream masthead row directly under it with boat name, sail number, TCC, design metadata
- Below: cream panel split into a working-log column (left, narrower) and §1 prose column (right, wider)
- Working log has a brass-tick on completed steps + a spinning loader on the active step, with `mm:ss.fff` stamps on the right edge
- Prose column shows partial text with a brass cursor at the streaming edge
- No `█▓▒` blocks anywhere
- No sticky CTA rail at the bottom yet (visible only after §1 streams)

Visual checklist for `/tmp/bench-sealed.png`:
- Working log still visible at top-left (it stays)
- §1 prose fully rendered on the right
- Below: 2-col grid of seven brass-bordered tiles (§2 — §8), each with a brass "Sealed · §N of 8" band
- Sticky CTA rail visible at the bottom of the viewport: charcoal background, brass "Send me the file — $XX" button on the right

Visual checklist for `/tmp/bench-cta.png`:
- Sticky CTA rail clearly visible at viewport bottom
- Rail contains: "Full file for <boat> — eight sections, ranked recommendations." on the left, brass primary button on the right
- No overlap between the rail and a footer (footer is hidden during the bench per Task 5)

If any item fails, fix the relevant component and re-run.

- [ ] **Step 4: Manually generate a test order to verify the report payoff**

The report page needs a real token. Use a test Stripe checkout (or, if a saved test report token exists, use it directly). Document the URL:

```bash
echo "Test report URL: https://dev.sailratings.com/report/<token-from-test-purchase>" > /tmp/report-test-url.txt
```

Then:

```bash
node tests/screenshot-report.mjs $(cat /tmp/report-test-url.txt | grep -oP 'https://\S+')
```

Visual checklist for `/tmp/report-compiling.png`:
- PinnedMasthead identical in position/styling to the bench's masthead (boat name, TCC, design metadata)
- Subline under masthead reads "File compiled. Opening…" in brass

Visual checklist for `/tmp/report-table.png`:
- PinnedMasthead still at top, subline gone
- Recommendations table is fully visible (header row + rows + expanded explanations)
- No prose yet (or prose container empty/animating-in)

Visual checklist for `/tmp/report-prose.png`:
- PinnedMasthead at top
- Recommendations table above
- Prose section visible below the table with text appearing
- RAI / Rivals not yet visible (they wait for prose to finish)

If a test order can't be generated easily, defer this step and capture a manual screenshot later — but **do not call the report payoff done without it**.

- [ ] **Step 5: Update tasks + memory**

Mark task #6 (Implement chosen conversion-flow direction) complete in TaskList. If the screenshots are clean, also note in the conversation that the flow is verified per `[[feedback-testing]]`.

- [ ] **Step 6: Final commit (if any cleanup edits were made during verification)**

```bash
cd /home/irc-data/code/sailratings
git status
# If clean, no commit. If edits, commit with a focused message describing the visual fix.
```

---

## Open questions to resolve with Stuart before/during execution

These are flagged in the spec; the plan codes assumptions but they should be confirmed:

1. **Sample PDF asset** — `samplePdfUrl` prop on `StickyCheckoutRail` is optional. The plan does NOT ship a sample PDF; the sample link will only render when Stuart provides one and `<TeaserAnalysis>` is given a `samplePdfUrl` prop (currently not passed from `page.tsx`). To enable, pass `samplePdfUrl="/samples/sail-ratings-sample.pdf"` to `<TeaserAnalysis>` and drop the PDF into `web/public/samples/`.

2. **Reassurance line copy** — currently "PDF delivered the moment payment clears · One certificate, one report". The spec mentioned "60-day re-rate guarantee" which is invented copy. The plan ships the truthful version; override via the `reassurance` prop if a real guarantee exists.

3. **Working log permanence** — the plan keeps the log visible after §1 streams. If Stuart wants it to collapse to a thin summary line after completion, that's a small follow-up (~10 lines of conditional rendering in `TeaserAnalysis.tsx`).

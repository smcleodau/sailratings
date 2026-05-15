"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowRight, Loader2, Check, X, Pencil } from "lucide-react";
import {
  streamInsights,
  createCheckoutSession,
  submitCorrection,
  type BoatDetail,
  type CorrectionField,
  type SSEStep,
} from "@/lib/api";
import { detectCurrency } from "@/lib/currency";

interface TeaserAnalysisProps {
  boat: BoatDetail;
  searchQuery?: string;
  onComplete?: (text: string) => void;
}

interface StepEntry extends SSEStep {
  state: "active" | "done";
}

type RedactedLine = { prefix: string; widths: number[] };
type Section = { title: string; description: string; lines: RedactedLine[] };

const SECTIONS: Section[] = [
  {
    title: "Where she sits",
    description: "Where your TCC sits today, and the one number that matters this season.",
    lines: [], // §1 is the visible one
  },
  {
    title: "Rating Drift",
    description:
      "How your TCC has moved across every IRC formula revision since you've owned the boat.",
    lines: [
      { prefix: "Year-on-year delta:", widths: [4, 7, 3] },
      { prefix: "Largest single jump:", widths: [6, 3, 8] },
      { prefix: "Cumulative drift since first cert:", widths: [9] },
    ],
  },
  {
    title: "Measurement Sensitivity",
    description:
      "Regression on every certificate input — which measurements are quietly costing you tenths.",
    lines: [
      { prefix: "Top lever:", widths: [11, 5] },
      { prefix: "Next two:", widths: [9, 4, 7] },
      { prefix: "Class-mean reference:", widths: [12] },
    ],
  },
  {
    title: "Fleet Performance",
    description:
      "Racing Advantage Index against your actual results: what the rating predicts vs what you sail.",
    lines: [
      { prefix: "RAI:", widths: [4, 8] },
      { prefix: "Wins / podiums:", widths: [2, 3, 4] },
      { prefix: "Strongest format:", widths: [10, 6] },
    ],
  },
  {
    title: "Sister Boats",
    description:
      "Side-by-side with every boat of your design on the register — where yours rates light, where it rates heavy.",
    lines: [
      { prefix: "Class certified:", widths: [3, 5] },
      { prefix: "You rate light vs:", widths: [10] },
      { prefix: "You rate heavy vs:", widths: [12] },
    ],
  },
  {
    title: "Head-to-Head Rivals",
    description:
      "The boats within ±0.005 TCC you're scored against most weekends, and how their certificates differ from yours.",
    lines: [
      { prefix: "Within ±0.005:", widths: [3, 5] },
      { prefix: "Top rival:", widths: [10, 3, 4] },
      { prefix: "Their declaration differs by:", widths: [11] },
    ],
  },
  {
    title: "Trial Certificate Model",
    description:
      "Re-rating scenarios costed out: does a re-measure actually move you, or are you near the floor.",
    lines: [
      { prefix: "Best scenario:", widths: [9, 4, 5] },
      { prefix: "Estimated saving:", widths: [4, 3, 7] },
      { prefix: "Floor distance:", widths: [9] },
    ],
  },
  {
    title: "Action Plan",
    description:
      "Ranked, specific measurement changes — what to do before your next certificate, in order of TCC return.",
    lines: [
      { prefix: "1.", widths: [12, 5] },
      { prefix: "2.", widths: [10, 7] },
      { prefix: "3.", widths: [9, 6, 4] },
    ],
  },
];

/** Build a "drafted but sealed" sketch line — prefix in pencil tone, then
 * varied █▓▒ blocks per width spec. Reads as analysis-on-tracing-paper. */
function RedactedSketch({ line }: { line: RedactedLine }) {
  // Stable per-prefix block characters (no random — must match SSR)
  const seed = line.prefix.length;
  const blocks = line.widths.map((w, i) => {
    // Vary block char a little for visual texture
    const palette = ["█", "▓", "▒"];
    const ch = palette[(seed + i) % palette.length];
    return ch.repeat(w);
  });
  return (
    <div className="font-mono text-[12px] text-charcoal/55 leading-[1.8]">
      <span className="text-charcoal/65">{line.prefix}</span>{" "}
      {blocks.map((b, i) => (
        <span key={i} className="text-charcoal/35 mr-1 select-none">
          {b}
        </span>
      ))}
    </div>
  );
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

/** Inline editable masthead field — click ? to correct, submits to /corrections. */
function MastheadField({
  boatId,
  label,
  value,
  fieldName,
  onPending,
}: {
  boatId: number;
  label: string;
  value: string | number | null | undefined;
  fieldName: CorrectionField;
  onPending: () => void;
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
      await submitCorrection(boatId, {
        field_name: fieldName,
        proposed_value: v,
      });
      setSubmitted(true);
      setEditing(false);
      onPending();
    } catch {
      // Silently swallow — moderation queue is best-effort
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
          className="bg-cream border border-brass/50 text-charcoal px-2 py-0.5 text-[13px] font-body w-44 focus:outline-none focus:border-brass"
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
      <span className="body-text text-charcoal text-[14px]">
        {value != null && String(value).trim() !== "" ? value : (
          <span className="text-charcoal/40 italic">unknown</span>
        )}
      </span>
      {submitted ? (
        <span className="data-mono text-[9px] uppercase tracking-wider text-signal-light ml-1">
          pending review
        </span>
      ) : (
        <button
          onClick={() => setEditing(true)}
          className="text-brass/60 hover:text-brass transition-colors ml-1 inline-flex items-center"
          aria-label={`Correct ${label.toLowerCase()}`}
          title={`Correct ${label.toLowerCase()}`}
        >
          <Pencil size={11} strokeWidth={1.75} />
        </button>
      )}
    </span>
  );
}

export default function TeaserAnalysis({
  boat,
  searchQuery,
  onComplete,
}: TeaserAnalysisProps) {
  const [steps, setSteps] = useState<StepEntry[]>([]);
  const [text, setText] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const [isDone, setIsDone] = useState(false);
  const [streamStarted, setStreamStarted] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isCheckingOut, setIsCheckingOut] = useState(false);
  const [checkoutError, setCheckoutError] = useState<string | null>(null);
  const [stickyVisible, setStickyVisible] = useState(false);
  const [compileMs, setCompileMs] = useState<number | null>(null);
  const startedRef = useRef(false);
  const textRef = useRef("");
  const proseRef = useRef<HTMLDivElement>(null);
  const currency = useMemo(() => detectCurrency(), []);
  const dateline = useMemo(() => nowStamp(), []);
  const [, forceRender] = useState(0);

  // Stream the report
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
      const startedAt = Date.now();
      const { track } = await import("@/lib/posthog");
      track("teaser_started", { boat_id: boat.id });

      try {
        const stream = streamInsights(boat.id, "free");
        for await (const event of stream) {
          if (cancelled) break;

          if (event.type === "step") {
            const stepData = event.data as SSEStep;
            setSteps((prev) => {
              const next: StepEntry[] = prev.map((s) =>
                s.state === "active" ? { ...s, state: "done" } : s,
              );
              next.push({ ...stepData, state: "active" });
              return next;
            });
          } else if (event.type === "text") {
            if (!streamStarted) {
              setStreamStarted(true);
              setSteps((prev) =>
                prev.map((s) =>
                  s.state === "active" ? { ...s, state: "done" } : s,
                ),
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
          const ms = Date.now() - startedAt;
          setCompileMs(ms);
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

  // Sticky CTA pill: show only after the owner has scrolled past the prose
  useEffect(() => {
    const handler = () => {
      const el = proseRef.current;
      if (!el) return;
      const rect = el.getBoundingClientRect();
      // Show pill once the prose top has scrolled above the viewport top
      setStickyVisible(rect.bottom < window.innerHeight * 0.6 && isDone);
    };
    handler();
    window.addEventListener("scroll", handler, { passive: true });
    return () => window.removeEventListener("scroll", handler);
  }, [isDone]);

  const handleCheckout = async (placement: "inline" | "sticky") => {
    setIsCheckingOut(true);
    setCheckoutError(null);
    const { track } = await import("@/lib/posthog");
    track("buy_clicked", {
      boat_id: boat.id,
      boat_name: boat.boat_name,
      currency: currency.code,
      search_query: searchQuery,
      placement: `bench_${placement}`,
    });
    try {
      const { checkout_url, order_token } = await createCheckoutSession({
        boat_id: boat.id,
        boat_name: boat.boat_name,
        currency: currency.code,
        search_query: searchQuery,
        teaser_text: textRef.current,
      });
      track("checkout_redirect", { boat_id: boat.id, order_token, currency: currency.code });
      window.location.href = checkout_url;
    } catch {
      track("buy_failed", { boat_id: boat.id, placement: `bench_${placement}` });
      setCheckoutError("Something went wrong. Please try again.");
      setIsCheckingOut(false);
    }
  };

  if (error && !text) {
    return (
      <div className="w-full max-w-3xl mx-auto px-6 py-12">
        <p className="body-text text-brass">{error}</p>
      </div>
    );
  }

  const tcc = boat.irc_tcc;
  const ratingLabel = tcc != null ? "IRC" : boat.orc_gph != null ? "ORC" : null;
  const ratingValue = tcc != null
    ? Number(tcc).toFixed(4)
    : boat.orc_gph != null
    ? Number(boat.orc_gph).toFixed(1)
    : null;
  const ratingUnit = tcc != null ? "TCC" : "GPH";

  const compileLine = (() => {
    const fragments: string[] = [];
    if (compileMs != null) fragments.push(`Compiled in ${(compileMs / 1000).toFixed(1)} s`);
    // Pull a few numbers from steps for the masthead stamp
    const finishStep = steps.find((s) => /race finishes/i.test(s.label));
    if (finishStep) {
      const m = finishStep.label.match(/(\d[\d,]*)\s+race\s+finishes/i);
      if (m) fragments.push(`${m[1]} finishes`);
      const e = finishStep.detail?.match(/(\d[\d,]*)\s+events?/i);
      if (e) fragments.push(`${e[1]} events`);
    }
    const sisterStep = steps.find((s) => /sister boats/i.test(s.label));
    if (sisterStep) {
      const m = sisterStep.label.match(/(\d[\d,]*)\s+sister/i);
      if (m) fragments.push(`${m[1]} sisters`);
    }
    return fragments.join("   ·   ");
  })();

  return (
    <article
      id="bench"
      className="relative w-full max-w-3xl mx-auto bg-cream/90 text-charcoal"
      style={{
        backgroundImage:
          "radial-gradient(rgba(44,44,44,0.025) 1px, transparent 1px)",
        backgroundSize: "12px 12px",
        boxShadow: "0 1px 0 rgba(44,44,44,0.04), 0 30px 80px -30px rgba(0,0,0,0.18)",
      }}
    >
      {/* Dateline */}
      <div className="flex items-baseline justify-between px-8 sm:px-12 pt-6 pb-3 border-b border-charcoal/15">
        <span className="data-mono text-[10px] uppercase tracking-[0.18em] text-brass font-semibold">
          Sail Ratings · The Bench
        </span>
        <span className="data-mono text-[10px] uppercase tracking-[0.18em] text-charcoal/65">
          {dateline}
        </span>
      </div>

      {/* Masthead */}
      <header className="px-8 sm:px-12 pt-8 pb-5">
        <div className="flex items-start justify-between gap-6 flex-wrap">
          <h1
            className="heading-display text-charcoal leading-[0.95]"
            style={{
              fontSize: "clamp(2.4rem, 5vw, 3.8rem)",
              letterSpacing: "-0.015em",
              textTransform: "uppercase",
            }}
          >
            {boat.boat_name}
          </h1>
          {ratingValue && (
            <div className="text-right">
              <div className="data-mono text-charcoal font-semibold" style={{ fontSize: "clamp(1.6rem, 3vw, 2.4rem)" }}>
                {ratingValue}
              </div>
              <div className="data-mono text-[10px] uppercase tracking-[0.18em] text-charcoal/70 mt-1 font-semibold">
                <span className="text-brass">{ratingLabel}</span>
                {" "}{ratingUnit}
              </div>
            </div>
          )}
        </div>

        {/* Metadata strip — inline editable */}
        <div className="mt-5 flex flex-wrap items-baseline gap-x-6 gap-y-2.5">
          {boat.sail_number && (
            <span className="data-mono text-[13px] text-charcoal/85 font-semibold">{boat.sail_number}</span>
          )}
          {boat.design && (
            <MastheadField
              boatId={boat.id}
              label="Design"
              value={boat.design}
              fieldName="design_canonical"
              onPending={() => forceRender((n) => n + 1)}
            />
          )}
          {boat.country && (
            <span className="data-mono text-[12px] uppercase tracking-[0.08em] text-charcoal/85 font-semibold border border-charcoal/15 px-1.5 py-0.5 rounded-sm">
              {boat.country}
            </span>
          )}
          <MastheadField
            boatId={boat.id}
            label="Year"
            value={boat.year_built}
            fieldName="year_built"
            onPending={() => forceRender((n) => n + 1)}
          />
          <MastheadField
            boatId={boat.id}
            label="Designer"
            value={boat.designer}
            fieldName="designer"
            onPending={() => forceRender((n) => n + 1)}
          />
          <MastheadField
            boatId={boat.id}
            label="Builder"
            value={boat.builder}
            fieldName="builder"
            onPending={() => forceRender((n) => n + 1)}
          />
        </div>

        {/* Compile stamp / live working steps */}
        {!isDone ? (
          <div className="mt-6 space-y-1.5">
            {steps.length === 0 ? (
              <div className="flex items-center gap-2 data-mono text-[11px] uppercase tracking-[0.14em] text-charcoal/65">
                <Loader2 size={11} className="animate-spin text-brass" />
                Opening the certificate registry…
              </div>
            ) : (
              steps.map((s, i) => (
                <div
                  key={i}
                  className="flex items-baseline gap-2 data-mono text-[11px] uppercase tracking-[0.12em] text-charcoal/75"
                >
                  <span className="flex-shrink-0 w-3">
                    {s.state === "done" ? (
                      <Check size={11} strokeWidth={2.5} className="text-signal-light" />
                    ) : (
                      <Loader2 size={10} className="animate-spin text-brass" />
                    )}
                  </span>
                  <span className="truncate">
                    {s.label}
                    {s.detail && (
                      <span className="text-charcoal/55 ml-2 normal-case tracking-normal">
                        {s.detail}
                      </span>
                    )}
                  </span>
                </div>
              ))
            )}
          </div>
        ) : (
          compileLine && (
            <div className="mt-6 data-mono text-[10px] uppercase tracking-[0.16em] text-charcoal/65 font-semibold">
              {compileLine}
            </div>
          )
        )}
      </header>

      {/* Brass hairline rule */}
      <div className="mx-8 sm:mx-12 h-px bg-brass/40" />

      {/* §1 — Where she sits */}
      <section ref={proseRef} className="px-8 sm:px-12 py-8 sm:py-10">
        <h2 className="flex items-baseline gap-4 mb-5">
          <span className="data-mono text-brass text-[12px] font-semibold tracking-[0.08em] flex-shrink-0">
            01
          </span>
          <span className="heading-display text-charcoal text-xl sm:text-2xl">
            {SECTIONS[0].title}
          </span>
        </h2>
        {text ? (
          <div className="body-text text-charcoal text-[17px] leading-[1.62] whitespace-pre-wrap" style={{ maxWidth: "62ch" }}>
            {text}
            {isStreaming && streamStarted && (
              <span className="inline-block w-0.5 h-5 bg-brass ml-0.5 align-text-bottom streaming-pulse" />
            )}
          </div>
        ) : (
          !streamStarted && (
            <div className="body-text text-charcoal/40 italic text-[15px]">
              Drafting…
            </div>
          )
        )}
      </section>

      {/* Brass hairline rule */}
      {isDone && <div className="mx-8 sm:mx-12 h-px bg-brass/40" />}

      {/* Redacted §2–§8 — drafted but sealed. Each section shows real-looking
          analysis-prefix lines with █▓▒ blocks where the specific values would
          sit. Reads as work-on-the-bench, not a TOC. */}
      {isDone && (
        <section className="px-8 sm:px-12 py-8" aria-label="Sealed sections">
          <div className="flex items-baseline justify-between mb-6">
            <div className="data-mono text-[10px] uppercase tracking-[0.18em] text-charcoal/70 font-semibold">
              Drafted · Seven sections sealed
            </div>
            <div className="data-mono text-[10px] uppercase tracking-[0.18em] text-charcoal/50 hidden sm:block">
              Hover to see what&rsquo;s in each
            </div>
          </div>
          <ol className="space-y-5">
            {SECTIONS.slice(1).map((sec, i) => {
              const num = i + 2;
              return (
                <li
                  key={num}
                  className="group flex items-baseline gap-5 border-l border-transparent hover:border-brass/40 pl-2 -ml-2 transition-colors"
                  title={sec.description}
                >
                  <span className="data-mono text-brass text-[12px] font-semibold tracking-[0.08em] flex-shrink-0 w-8 pt-0.5">
                    {String(num).padStart(2, "0")}
                  </span>
                  <div className="flex-1 min-w-0">
                    <div className="heading-display text-charcoal text-[15px] sm:text-[17px] font-semibold uppercase tracking-[0.02em] mb-1.5">
                      {sec.title}
                    </div>
                    <div className="space-y-0">
                      {sec.lines.map((line, li) => (
                        <RedactedSketch key={li} line={line} />
                      ))}
                    </div>
                  </div>
                </li>
              );
            })}
          </ol>
        </section>
      )}

      {/* Inline CTA at the bottom of the bench */}
      {isDone && (
        <div className="bg-navy text-cream px-8 sm:px-12 py-7 sm:py-8">
          <div className="flex flex-col sm:flex-row sm:items-center gap-5 sm:gap-8 justify-between">
            <div className="flex-1 min-w-0">
              <p className="body-text text-cream text-[15px] sm:text-base leading-relaxed">
                Seven more sections drafted for{" "}
                <span className="font-semibold">{boat.boat_name}</span>. Open the file
                and see what to change, in what order, for what return.
              </p>
              <p className="body-text text-cream/60 text-[12px] mt-2">
                Delivered as a PDF the moment payment clears. One certificate, one report.
              </p>
            </div>
            <div className="flex-shrink-0">
              <button
                onClick={() => void handleCheckout("inline")}
                disabled={isCheckingOut}
                className="group inline-flex items-center gap-3 bg-brass text-navy px-6 py-3.5 text-[13px] font-body font-semibold uppercase tracking-[0.08em] hover:bg-cream active:translate-y-px transition-all disabled:opacity-60"
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
              {checkoutError && (
                <p className="body-text text-[12px] text-brass mt-2 text-right">{checkoutError}</p>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Sticky lower-right CTA pill (only after scrolling past §1) */}
      {isDone && stickyVisible && (
        <div className="fixed bottom-6 right-6 z-40 animate-in fade-in">
          <button
            onClick={() => void handleCheckout("sticky")}
            disabled={isCheckingOut}
            className="inline-flex items-center gap-2 bg-navy text-cream px-5 py-3 text-[12px] font-body font-semibold uppercase tracking-[0.1em] shadow-2xl shadow-black/40 hover:bg-charcoal active:translate-y-px transition-all"
          >
            {currency.display} — full file
            <ArrowRight size={12} strokeWidth={2.5} />
          </button>
        </div>
      )}
    </article>
  );
}

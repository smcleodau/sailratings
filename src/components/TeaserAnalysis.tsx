"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Check, Lock, Loader2, ArrowRight } from "lucide-react";
import {
  streamInsights,
  createCheckoutSession,
  type SSEStep,
} from "@/lib/api";
import { detectCurrency } from "@/lib/currency";

interface TeaserAnalysisProps {
  boatId: number;
  boatName: string;
  searchQuery?: string;
  onComplete?: (text: string) => void;
}

interface StepEntry extends SSEStep {
  state: "active" | "done";
}

const SECTIONS: { title: string; description: string }[] = [
  {
    title: "Executive Summary",
    description:
      "Where your TCC sits today, and the one number that matters this season.",
  },
  {
    title: "Rating Drift",
    description:
      "How your TCC has moved across every IRC formula revision since you've owned the boat.",
  },
  {
    title: "Measurement Sensitivity",
    description:
      "Regression on every certificate input — which measurements are quietly costing you tenths.",
  },
  {
    title: "Fleet Performance",
    description:
      "Racing Advantage Index against your actual results: what the rating predicts vs what you sail.",
  },
  {
    title: "Sister Boats",
    description:
      "Side-by-side with every boat of your design on the register — where yours rates light, where it rates heavy.",
  },
  {
    title: "Head-to-Head Rivals",
    description:
      "The boats within ±0.005 TCC you're scored against most weekends, and how their certificates differ from yours.",
  },
  {
    title: "Trial Certificate Model",
    description:
      "Re-rating scenarios costed out: does a re-measure actually move you, or are you near the floor.",
  },
  {
    title: "Action Plan",
    description:
      "Ranked, specific measurement changes — what to do before your next certificate, in order of TCC return.",
  },
];

export default function TeaserAnalysis({
  boatId,
  boatName,
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
  const startedRef = useRef(false);
  const textRef = useRef("");
  const currency = useMemo(() => detectCurrency(), []);

  useEffect(() => {
    if (startedRef.current) return;
    startedRef.current = true;

    let cancelled = false;
    let lastStepAddedAt = 0;

    async function run() {
      setIsStreaming(true);
      setError(null);
      setText("");
      textRef.current = "";
      setSteps([]);
      setStreamStarted(false);
      const startedAt = Date.now();
      const { track } = await import("@/lib/posthog");
      track("teaser_started", { boat_id: boatId });

      try {
        const stream = streamInsights(boatId, "free");
        for await (const event of stream) {
          if (cancelled) break;

          if (event.type === "step") {
            const stepData = event.data as SSEStep;
            // Mark previous step done, push new one as active
            setSteps((prev) => {
              const next: StepEntry[] = prev.map((s) =>
                s.state === "active" ? { ...s, state: "done" } : s,
              );
              next.push({ ...stepData, state: "active" });
              return next;
            });
            lastStepAddedAt = Date.now();
          } else if (event.type === "text") {
            // First text event — mark final step done
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
        if (!cancelled) {
          setError("Failed to load analysis. Please try again.");
        }
      } finally {
        if (!cancelled) {
          setIsStreaming(false);
          setIsDone(true);
          // Mark any remaining active step done
          setSteps((prev) =>
            prev.map((s) =>
              s.state === "active" ? { ...s, state: "done" } : s,
            ),
          );
          track("teaser_completed", {
            boat_id: boatId,
            duration_ms: Date.now() - startedAt,
            char_count: textRef.current.length,
            had_error: !!error,
            step_count: lastStepAddedAt ? steps.length : 0,
          });
          if (textRef.current && onComplete) {
            onComplete(textRef.current);
          }
        }
      }
    }

    run();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [boatId]);

  const handleCheckout = async () => {
    setIsCheckingOut(true);
    setCheckoutError(null);

    const { track } = await import("@/lib/posthog");
    track("buy_clicked", {
      boat_id: boatId,
      boat_name: boatName,
      currency: currency.code,
      search_query: searchQuery,
      placement: "teaser_inline",
    });

    try {
      const { checkout_url, order_token } = await createCheckoutSession({
        boat_id: boatId,
        boat_name: boatName,
        currency: currency.code,
        search_query: searchQuery,
        teaser_text: textRef.current,
      });
      track("checkout_redirect", {
        boat_id: boatId,
        order_token,
        currency: currency.code,
      });
      window.location.href = checkout_url;
    } catch {
      track("buy_failed", { boat_id: boatId, placement: "teaser_inline" });
      setCheckoutError("Something went wrong. Please try again.");
      setIsCheckingOut(false);
    }
  };

  if (error && !text) {
    return (
      <div className="w-full max-w-3xl mx-auto border border-border bg-white p-8">
        <p className="body-text text-brass">{error}</p>
      </div>
    );
  }

  return (
    <div className="w-full max-w-3xl mx-auto border border-border bg-white">
      {/* Header */}
      <div className="border-b border-border-light px-8 py-5 sm:px-10 flex items-baseline justify-between flex-wrap gap-3">
        <div>
          <div className="data-mono text-[10px] uppercase tracking-[0.14em] text-muted/70 mb-1">
            Rating File
          </div>
          <h3 className="heading-display text-xl sm:text-2xl text-charcoal">
            {boatName}{" "}
            <span className="text-muted font-normal text-base sm:text-lg">
              — Executive Summary
            </span>
          </h3>
        </div>
        <div className="data-mono text-[11px] text-muted/60">
          Section 1 of 8
        </div>
      </div>

      {/* Working steps */}
      {(steps.length > 0 || !streamStarted) && (
        <div className="border-b border-border-light px-8 py-5 sm:px-10 bg-cream/30">
          <div className="data-mono text-[10px] uppercase tracking-[0.14em] text-muted/70 mb-3">
            Compiling the report
          </div>
          <ul className="space-y-2">
            {steps.map((s, i) => (
              <li key={i} className="flex items-start gap-3 text-[13px] sm:text-sm">
                <span className="flex-shrink-0 mt-0.5 w-4 h-4">
                  {s.state === "done" ? (
                    <Check size={16} strokeWidth={2.25} className="text-signal-light" />
                  ) : (
                    <Loader2 size={14} strokeWidth={2} className="text-brass animate-spin" />
                  )}
                </span>
                <span className="flex-1 min-w-0">
                  <span className={`body-text ${s.state === "done" ? "text-charcoal" : "text-charcoal"}`}>
                    {s.label}
                  </span>
                  {s.detail && (
                    <span className="data-mono text-[11px] text-muted/80 ml-2">
                      · {s.detail}
                    </span>
                  )}
                </span>
              </li>
            ))}
            {!streamStarted && steps.length === 0 && (
              <li className="flex items-center gap-3 text-[13px]">
                <Loader2 size={14} strokeWidth={2} className="text-brass animate-spin" />
                <span className="body-text text-muted italic">
                  Opening the certificate registry…
                </span>
              </li>
            )}
          </ul>
        </div>
      )}

      {/* Streamed analysis */}
      <div className="px-8 py-7 sm:px-10 sm:py-9">
        {text && (
          <div className="body-text text-charcoal-light text-base sm:text-[17px] leading-relaxed whitespace-pre-wrap">
            {text}
            {isStreaming && streamStarted && (
              <span className="inline-block w-0.5 h-5 bg-brass ml-0.5 align-text-bottom streaming-pulse" />
            )}
          </div>
        )}
      </div>

      {/* Section TOC — what's locked */}
      {isDone && (
        <>
          <div className="border-t border-border-light px-8 py-5 sm:px-10 bg-cream/20">
            <div className="data-mono text-[10px] uppercase tracking-[0.14em] text-muted/70 mb-3">
              The full report — 8 sections
            </div>
            <ol className="space-y-2">
              {SECTIONS.map((sec, i) => {
                const locked = i > 0;
                return (
                  <li
                    key={i}
                    className={`flex items-baseline gap-3 text-[13px] ${
                      locked ? "" : ""
                    }`}
                  >
                    <span className="data-mono text-muted/60 w-5 flex-shrink-0 text-right">
                      {i + 1}.
                    </span>
                    <span className="flex-1 min-w-0">
                      <span
                        className={`body-text font-medium ${
                          locked ? "text-charcoal" : "text-charcoal"
                        }`}
                      >
                        {sec.title}
                      </span>
                      <span className="body-text text-muted ml-2">
                        — {sec.description}
                      </span>
                    </span>
                    <span className="flex-shrink-0 w-4">
                      {locked ? (
                        <Lock size={12} strokeWidth={1.75} className="text-brass/70" />
                      ) : (
                        <Check size={14} strokeWidth={2} className="text-signal-light" />
                      )}
                    </span>
                  </li>
                );
              })}
            </ol>
          </div>

          {/* Inline CTA panel */}
          <div className="border-t border-brass/30 px-8 py-7 sm:px-10 sm:py-8 bg-gradient-to-b from-cream/60 to-cream/30">
            <div className="flex flex-col sm:flex-row items-start sm:items-center gap-6 justify-between">
              <div className="flex-1 min-w-0">
                <p className="body-text text-charcoal text-base sm:text-lg leading-relaxed">
                  Section&nbsp;1 covers where you sit. Sections&nbsp;2 through&nbsp;8
                  cover what to change, in what order, for what return.
                </p>
                <p className="body-text text-muted text-[13px] mt-2">
                  Delivered as a PDF the moment payment clears. One certificate, one report, yours to keep.
                </p>
              </div>
              <div className="flex-shrink-0 w-full sm:w-auto">
                <button
                  onClick={handleCheckout}
                  disabled={isCheckingOut}
                  className="group inline-flex items-center justify-center gap-3 bg-navy text-cream px-6 py-3.5 text-[14px] font-body font-semibold tracking-wide hover:bg-charcoal active:translate-y-px transition-all w-full sm:w-auto disabled:opacity-60 disabled:cursor-not-allowed rounded-sm"
                >
                  {isCheckingOut ? (
                    <>
                      <Loader2 size={16} strokeWidth={2} className="animate-spin" />
                      Opening checkout…
                    </>
                  ) : (
                    <>
                      Open the full report — {currency.display}
                      <ArrowRight
                        size={16}
                        strokeWidth={2}
                        className="transition-transform group-hover:translate-x-1"
                      />
                    </>
                  )}
                </button>
                {checkoutError && (
                  <p className="body-text text-xs text-brass mt-2 text-right">
                    {checkoutError}
                  </p>
                )}
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

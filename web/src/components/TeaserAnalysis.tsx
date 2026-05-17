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
  /** Number of sealed §-cards revealed so far. Drives a slow, continuous reveal
   *  rather than dumping all seven at the same moment. */
  const [sealedRevealedCount, setSealedRevealedCount] = useState(0);
  const startedRef = useRef(false);
  const textRef = useRef("");
  const startTimeRef = useRef<number>(0);
  const currency = useMemo(() => detectCurrency(), []);
  const allSealedOut = sealedRevealedCount >= 7;

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

  // Slow, continuous reveal of the seven sealed §-cards. First card materialises
  // 5 s after mount (while §1 prose is still streaming), each subsequent card
  // ~1.6 s after. By card 7 we're around 14-15 s in. The CTA waits for both
  // streaming to finish AND all cards to be out before it slides into view —
  // so nothing ever "clumps" at the end.
  useEffect(() => {
    const timeouts: ReturnType<typeof setTimeout>[] = [];
    const FIRST_AT_MS = 5000;
    const GAP_MS = 1600;
    for (let i = 1; i <= 7; i++) {
      timeouts.push(
        setTimeout(() => setSealedRevealedCount((c) => Math.max(c, i)), FIRST_AT_MS + (i - 1) * GAP_MS),
      );
    }
    return () => {
      timeouts.forEach(clearTimeout);
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
        {/* Two-column bench: working log (left) + §1 prose (right). On mobile, stacks.
            min-height keeps the bench filling the viewport during streaming so the chrome doesn't sit on a sea of empty cream. */}
        <section className="grid grid-cols-1 md:grid-cols-[minmax(240px,1fr)_2.5fr] gap-0 border-b border-charcoal/15 md:min-h-[calc(100vh-72px-100px)]">
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
                      <span className="block leading-snug">{s.label}</span>
                      {s.detail && (
                        <span className="block text-charcoal/55 normal-case tracking-normal text-[11px] mt-0.5 leading-snug">
                          {s.detail}
                        </span>
                      )}
                    </span>
                    <span className="data-mono text-[9px] text-charcoal/40 ml-1 flex-shrink-0 tabular-nums">
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

        {/* Brass hairline rule appears as soon as the first sealed card materialises. */}
        {sealedRevealedCount > 0 && <div className="mx-6 sm:mx-12 h-px bg-brass/40" />}

        {/* Sealed sections grid — sections reveal continuously, one at a time,
            beginning while §1 prose is still streaming. */}
        <SealedSectionGrid revealedCount={sealedRevealedCount} />
      </article>

      {/* Persistent CTA rail — only once §1 has finished AND all seven cards are out.
          Prevents any "clump" at the end of the experience. */}
      <StickyCheckoutRail
        visible={isDone && allSealedOut}
        boatName={boat.boat_name}
        onCheckout={handleCheckout}
        samplePdfUrl={samplePdfUrl}
      />
    </>
  );
}

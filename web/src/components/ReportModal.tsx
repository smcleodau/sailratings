"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { X } from "lucide-react";
import {
  streamInsights,
  createCheckoutSession,
  type BoatDetail,
} from "@/lib/api";
import { detectCurrency } from "@/lib/currency";
import PinnedMasthead from "@/components/PinnedMasthead";
import SealedSectionGrid from "@/components/SealedSectionGrid";
import StickyCheckoutRail from "@/components/StickyCheckoutRail";

interface ReportModalProps {
  boat: BoatDetail;
  searchQuery?: string;
  onClose: () => void;
  samplePdfUrl?: string;
}

type Phase = "thinking" | "brief";

export default function ReportModal({
  boat,
  searchQuery,
  onClose,
  samplePdfUrl,
}: ReportModalProps) {
  const [thinking, setThinking] = useState("");
  const [brief, setBrief] = useState("");
  const [phase, setPhase] = useState<Phase>("thinking");
  const [isStreamingThinking, setIsStreamingThinking] = useState(false);
  const [isStreamingBrief, setIsStreamingBrief] = useState(false);
  const [briefDone, setBriefDone] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sealedRevealedCount, setSealedRevealedCount] = useState(0);

  const thinkingRef = useRef("");
  const briefRef = useRef("");
  const startedRef = useRef(false);
  const startTimeRef = useRef(0);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  const currency = useMemo(() => detectCurrency(), []);
  const allSealedOut = sealedRevealedCount >= 7;

  // ── ESC to close, body-scroll lock ────────────────────────────────────
  useEffect(() => {
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => {
      document.body.style.overflow = prevOverflow;
      window.removeEventListener("keydown", onKey);
    };
  }, [onClose]);

  // ── Auto-scroll the thinking pane as words arrive ─────────────────────
  useEffect(() => {
    if (phase !== "thinking" || !scrollRef.current) return;
    const el = scrollRef.current;
    // Only stick to the bottom if the user hasn't scrolled away.
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
    if (nearBottom) {
      el.scrollTop = el.scrollHeight;
    }
  }, [thinking, phase]);

  // ── Sealed-section staged reveal — starts when the brief begins ────────
  useEffect(() => {
    if (!isStreamingBrief && !briefDone) return;
    const timeouts: ReturnType<typeof setTimeout>[] = [];
    const FIRST_AT_MS = 5000;
    const GAP_MS = 1600;
    for (let i = 1; i <= 7; i++) {
      timeouts.push(
        setTimeout(
          () => setSealedRevealedCount((c) => Math.max(c, i)),
          FIRST_AT_MS + (i - 1) * GAP_MS,
        ),
      );
    }
    return () => {
      timeouts.forEach(clearTimeout);
    };
  }, [isStreamingBrief, briefDone]);

  // ── Open the stream once per boat ──────────────────────────────────────
  useEffect(() => {
    if (startedRef.current) return;
    startedRef.current = true;
    let cancelled = false;

    async function run() {
      setError(null);
      setThinking("");
      setBrief("");
      setPhase("thinking");
      setIsStreamingThinking(true);
      setIsStreamingBrief(false);
      setBriefDone(false);
      thinkingRef.current = "";
      briefRef.current = "";
      startTimeRef.current = Date.now();

      const { track } = await import("@/lib/posthog");
      track("teaser_started", { boat_id: boat.id, surface: "modal" });

      try {
        const stream = streamInsights(boat.id, "free", "prose");
        for await (const event of stream) {
          if (cancelled) break;
          if (event.type === "thought_chunk") {
            thinkingRef.current += event.data as string;
            setThinking(thinkingRef.current);
          } else if (event.type === "phase") {
            if (event.data === "report") {
              setPhase("brief");
              setIsStreamingThinking(false);
              setIsStreamingBrief(true);
            }
          } else if (event.type === "text") {
            // Defensive: some boats may stream text without an explicit phase.
            if (!isStreamingBrief) {
              setPhase("brief");
              setIsStreamingThinking(false);
              setIsStreamingBrief(true);
            }
            briefRef.current += event.data as string;
            setBrief(briefRef.current);
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
          setIsStreamingThinking(false);
          setIsStreamingBrief(false);
          setBriefDone(true);
          const ms = Date.now() - startTimeRef.current;
          track("teaser_completed", {
            boat_id: boat.id,
            duration_ms: ms,
            thinking_chars: thinkingRef.current.length,
            brief_chars: briefRef.current.length,
            surface: "modal",
            had_error: !!error,
          });
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
      placement: "modal-rail",
    });
    const { checkout_url, order_token } = await createCheckoutSession({
      boat_id: boat.id,
      boat_name: boat.boat_name,
      currency: currency.code,
      search_query: searchQuery,
      teaser_text: briefRef.current,
    });
    track("checkout_redirect", {
      boat_id: boat.id,
      order_token,
      currency: currency.code,
    });
    window.location.href = checkout_url;
  };

  // Split thinking into paragraphs for nicer rendering.
  const thinkingParagraphs = thinking.split(/\n{2,}/);

  return (
    <div
      className="fixed inset-0 z-[100] flex items-end sm:items-center justify-center"
      role="dialog"
      aria-modal="true"
      aria-label={`Owner brief for ${boat.boat_name}`}
    >
      {/* Backdrop ─ navy with subtle blur, fades in */}
      <div
        className="absolute inset-0 bg-navy/85 backdrop-blur-sm dossier-backdrop"
        onClick={onClose}
        aria-hidden
      />

      {/* Modal frame */}
      <div
        className="relative w-full sm:w-[min(96vw,1100px)] h-[96vh] sm:h-[94vh] bg-cream text-charcoal shadow-[0_30px_80px_-20px_rgba(0,0,0,0.55)] dossier-in dossier-paper flex flex-col"
        style={{
          // sharp corners, brass top edge, hairline brass on the rest
          borderTop: "3px solid var(--color-brass)",
          borderLeft: "1px solid rgba(194,155,97,0.55)",
          borderRight: "1px solid rgba(194,155,97,0.55)",
          borderBottom: "1px solid rgba(194,155,97,0.55)",
        }}
      >
        {/* Top utility bar — file label + close button */}
        <div className="flex-shrink-0 flex items-center justify-between px-5 sm:px-8 h-9 border-b border-charcoal/10 bg-cream">
          <span className="data-mono text-[10px] uppercase tracking-[0.22em] text-charcoal/55 font-semibold">
            Confidential · Owner brief
          </span>
          <button
            onClick={onClose}
            className="group inline-flex items-center gap-1.5 data-mono text-[10px] uppercase tracking-[0.18em] text-charcoal/60 hover:text-charcoal transition-colors"
            aria-label="Close report"
          >
            <span>Close</span>
            <X size={13} strokeWidth={2.25} className="text-brass group-hover:rotate-90 transition-transform" />
          </button>
        </div>

        {/* Scroll body */}
        <div ref={scrollRef} className="flex-1 overflow-y-auto overscroll-contain">
          {/* Pinned boat masthead inside the modal's scroll */}
          <PinnedMasthead
            boat={boat}
            subline={
              phase === "thinking" ? (
                <span>
                  Opening the file<span className="dossier-caret" />
                </span>
              ) : briefDone ? (
                "File compiled · Eight sections available"
              ) : (
                <span>
                  Drafting the brief<span className="dossier-caret" />
                </span>
              )
            }
          />

          <article
            id="bench"
            className="max-w-3xl mx-auto px-5 sm:px-12 pt-8 pb-40"
          >
            {/* ── Thinking pane ──────────────────────────────────────── */}
            <section aria-label="Reading the file" className="mb-10">
              <div className="flex items-center gap-3 mb-5">
                <span className="data-mono text-[10px] uppercase tracking-[0.22em] text-brass font-semibold">
                  Reading the file
                </span>
                <span className="h-px flex-1 bg-brass/30" />
              </div>

              <div className="dossier-marginalia">
                {thinking ? (
                  <div
                    className="body-text italic text-charcoal/85 text-[15.5px] sm:text-[16.5px] leading-[1.78]"
                    style={{ maxWidth: "62ch" }}
                  >
                    {thinkingParagraphs.map((p, i) => (
                      <p key={i} className={i > 0 ? "mt-4" : ""}>
                        {p}
                        {isStreamingThinking && i === thinkingParagraphs.length - 1 && (
                          <span className="dossier-caret" />
                        )}
                      </p>
                    ))}
                  </div>
                ) : (
                  <p className="body-text italic text-charcoal/45 text-[15px]">
                    Pulling her file off the shelf
                    <span className="dossier-caret" />
                  </p>
                )}
              </div>
            </section>

            {/* ── Phase divider: brass hairline + label ───────────────── */}
            {phase === "brief" && (
              <div className="my-10">
                <div className="h-px bg-brass/55 dossier-brass-draw" />
                <div className="mt-5 flex items-center gap-3">
                  <span className="data-mono text-[10px] uppercase tracking-[0.18em] text-brass font-semibold dossier-label-in">
                    The brief
                  </span>
                  <span className="data-mono text-[10px] uppercase tracking-[0.18em] text-charcoal/45 dossier-label-in">
                    Section 01 of 08 · Executive summary
                  </span>
                </div>
              </div>
            )}

            {/* ── Brief pane ──────────────────────────────────────────── */}
            {phase === "brief" && (
              <section aria-label="Executive summary">
                {brief ? (
                  <div
                    className="body-text text-charcoal text-[16.5px] sm:text-[17.5px] leading-[1.66] whitespace-pre-wrap"
                    style={{ maxWidth: "62ch" }}
                  >
                    {brief}
                    {isStreamingBrief && <span className="dossier-caret" />}
                  </div>
                ) : (
                  <p className="body-text text-charcoal/40 italic text-[15px]">
                    Composing
                    <span className="dossier-caret" />
                  </p>
                )}
              </section>
            )}

            {/* Error state */}
            {error && (
              <p className="body-text text-brass text-[14px] mt-6">{error}</p>
            )}

            {/* ── Sealed sections — what's behind the paywall ─────────── */}
            {phase === "brief" && sealedRevealedCount > 0 && (
              <>
                <div className="h-px bg-brass/30 mt-12" />
                <SealedSectionGrid revealedCount={sealedRevealedCount} />
              </>
            )}
          </article>
        </div>

        {/* CTA rail anchored to the modal's bottom edge. Rendered with absolute
            positioning inside the modal so it doesn't escape into the document.
            We hide the global StickyCheckoutRail's fixed positioning by wrapping
            it in an absolute container — the rail itself uses position:fixed,
            but visually it pinned to the same place anyway. We re-implement here
            so it scopes to the modal. */}
        <div className="absolute left-0 right-0 bottom-0">
          {briefDone && allSealedOut && (
            <StickyCheckoutRail
              visible
              boatName={boat.boat_name}
              onCheckout={handleCheckout}
              samplePdfUrl={samplePdfUrl}
            />
          )}
        </div>
      </div>
    </div>
  );
}

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

  const handleCheckout = async () => {
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
      <div className="max-w-6xl mx-auto px-6 sm:px-10 py-3.5 sm:py-4 flex flex-col sm:flex-row items-stretch sm:items-center gap-3 sm:gap-8">
        <div className="flex-1 min-w-0">
          <p className="body-text text-cream text-[14px] sm:text-[15px] leading-snug">
            Full file for <span className="font-semibold">{boatName}</span> — eight sections, ranked recommendations.
          </p>
          <span className="data-mono text-cream/55 text-[10.5px] uppercase tracking-[0.14em] mt-1 hidden sm:block">
            {reassurance}
          </span>
        </div>
        <div className="flex flex-col items-end gap-2 flex-shrink-0">
          <div className="flex items-center gap-3">
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
              onClick={() => void handleCheckout()}
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
          {error && (
            <span className="body-text text-[12px] text-brass">{error}</span>
          )}
        </div>
      </div>
    </div>
  );
}

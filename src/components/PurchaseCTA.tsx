"use client";

import { useState, useMemo } from "react";
import {
  BarChart3,
  Ruler,
  Scale,
  Swords,
  FileCheck,
  ArrowRight,
  Loader2,
} from "lucide-react";
import { createCheckoutSession } from "@/lib/api";
import { detectCurrency } from "@/lib/currency";

interface PurchaseCTAProps {
  boatId: number;
  boatName: string;
  searchQuery?: string;
  teaserText?: string;
}

const FEATURES = [
  {
    icon: BarChart3,
    text: "Ranked optimisation recommendations \u2014 biggest gains first",
  },
  {
    icon: Ruler,
    text: "Your measurements vs the fleet \u2014 see where you\u2019re giving away rating",
  },
  {
    icon: Scale,
    text: "How recent rule changes have affected your certificate",
  },
  {
    icon: Swords,
    text: "Head-to-head rating comparison with your closest competitors",
  },
  {
    icon: FileCheck,
    text: "Trial certificate modelling \u2014 know which changes are worth the investment",
  },
];

export default function PurchaseCTA({
  boatId,
  boatName,
  searchQuery,
  teaserText,
}: PurchaseCTAProps) {
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const currency = useMemo(() => detectCurrency(), []);

  const handlePurchase = async () => {
    setIsLoading(true);
    setError(null);

    try {
      const { checkout_url } = await createCheckoutSession({
        boat_id: boatId,
        boat_name: boatName,
        currency: currency.code,
        search_query: searchQuery,
        teaser_text: teaserText,
      });
      window.location.href = checkout_url;
    } catch {
      setError("Something went wrong. Please try again.");
      setIsLoading(false);
    }
  };

  return (
    <div className="w-full max-w-2xl mx-auto">
      <div className="border border-border bg-white">
        {/* Header */}
        <div className="px-8 py-8 sm:px-12 sm:py-10 border-b border-border-light">
          <h3 className="heading-serif text-2xl sm:text-3xl text-ink mb-3">
            Unlock the Full Analysis
          </h3>
          <p className="body-text text-muted text-base sm:text-lg">
            Every tenth of a point matters. See exactly where{" "}
            <span className="text-ink font-medium">{boatName}</span> is leaving
            performance on the table.
          </p>
        </div>

        {/* Features */}
        <div className="px-8 py-6 sm:px-12 sm:py-8">
          <ul className="space-y-4">
            {FEATURES.map((feature, index) => (
              <li key={index} className="flex items-start gap-4">
                <feature.icon
                  size={20}
                  strokeWidth={1.5}
                  className="text-copper mt-0.5 flex-shrink-0"
                />
                <span className="body-text text-ink-light text-base">
                  {feature.text}
                </span>
              </li>
            ))}
          </ul>
        </div>

        {/* Price + CTA */}
        <div className="px-8 py-8 sm:px-12 sm:py-10 border-t border-border-light">
          <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-6">
            <div>
              <div className="flex items-baseline gap-2">
                <span className="heading-serif text-4xl text-ink">
                  {currency.display}
                </span>
                <span className="body-text text-muted text-sm">
                  {currency.code}
                </span>
              </div>
              <p className="body-text text-xs text-muted mt-1">
                One-time purchase. Delivered instantly via email.
              </p>
            </div>
            <div className="w-full sm:w-auto">
              <button
                onClick={handlePurchase}
                disabled={isLoading}
                className="group flex items-center gap-3 bg-copper text-white px-8 py-4 text-base font-body font-medium tracking-wide hover:bg-copper-dark transition-colors cursor-pointer w-full sm:w-auto justify-center disabled:opacity-60 disabled:cursor-not-allowed"
                style={{ borderRadius: "1px" }}
              >
                {isLoading ? (
                  <>
                    <Loader2
                      size={18}
                      strokeWidth={1.5}
                      className="animate-spin"
                    />
                    Processing...
                  </>
                ) : (
                  <>
                    Get the Full Report
                    <ArrowRight
                      size={18}
                      strokeWidth={1.5}
                      className="transition-transform group-hover:translate-x-1"
                    />
                  </>
                )}
              </button>
              {error && (
                <p className="body-text text-xs text-copper mt-2">{error}</p>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

"use client";

import {
  BarChart3,
  Ruler,
  Scale,
  Swords,
  FileCheck,
  ArrowRight,
} from "lucide-react";

interface PurchaseCTAProps {
  boatId: number;
  boatName: string;
}

const FEATURES = [
  {
    icon: BarChart3,
    text: "Specific optimisation recommendations ranked by impact",
  },
  {
    icon: Ruler,
    text: "Your measurements vs class averages — where you're giving away points",
  },
  {
    icon: Scale,
    text: "Rule change impact analysis on your certificate",
  },
  {
    icon: Swords,
    text: "Head-to-head comparison with your closest rivals",
  },
  {
    icon: FileCheck,
    text: "Trial certificate scenarios — what changes are worth pursuing",
  },
];

export default function PurchaseCTA({ boatId, boatName }: PurchaseCTAProps) {
  const handlePurchase = () => {
    console.log(
      `[Stripe Checkout] Initiating purchase for boat ${boatId} (${boatName}) — $99 USD`
    );
    // Stripe Checkout integration placeholder
    // In production: redirect to Stripe Checkout session
    // with boat_id in metadata and currency localisation
  };

  return (
    <div className="w-full max-w-2xl mx-auto">
      <div className="border border-border bg-white">
        {/* Header */}
        <div className="px-8 py-8 sm:px-12 sm:py-10 border-b border-border-light">
          <h3 className="heading-serif text-2xl sm:text-3xl text-ink mb-3">
            Get Your Full Rating Report
          </h3>
          <p className="body-text text-muted text-base sm:text-lg">
            A comprehensive analysis of{" "}
            <span className="text-ink font-medium">{boatName}</span>
            &rsquo;s IRC rating, with actionable recommendations to find every
            hidden point.
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
                <span className="heading-serif text-4xl text-ink">$99</span>
                <span className="body-text text-muted text-sm">USD</span>
              </div>
              <p className="body-text text-xs text-muted mt-1">
                One-time purchase. Delivered instantly via email.
              </p>
            </div>
            <button
              onClick={handlePurchase}
              className="group flex items-center gap-3 bg-navy text-white px-8 py-4 text-base font-body font-medium tracking-wide hover:bg-navy-light transition-colors cursor-pointer w-full sm:w-auto justify-center"
              style={{ borderRadius: "1px" }}
            >
              Purchase Report
              <ArrowRight
                size={18}
                strokeWidth={1.5}
                className="transition-transform group-hover:translate-x-1"
              />
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

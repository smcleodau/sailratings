import Link from "next/link";
import { ArrowRight } from "lucide-react";

interface FunnelCTAProps {
  /** Headline copy on the navy block. Boat names should be wrapped in <em>. */
  headline: React.ReactNode;
  /** Smaller line beneath the headline. Optional. */
  subline?: React.ReactNode;
  /** Button label. Defaults to "Pull the file". */
  buttonLabel?: string;
  /** Where the button goes. Defaults to "/" (the Bench). */
  buttonHref?: string;
}

export default function FunnelCTA({
  headline,
  subline,
  buttonLabel = "Pull the file",
  buttonHref = "/",
}: FunnelCTAProps) {
  return (
    <section className="bg-navy text-cream">
      <div className="max-w-4xl mx-auto px-8 sm:px-12 py-10 sm:py-14">
        <div className="flex flex-col sm:flex-row sm:items-center gap-6 sm:gap-10 justify-between">
          <div className="flex-1 min-w-0">
            <p className="body-text text-cream text-[16px] sm:text-[17px] leading-relaxed">
              {headline}
            </p>
            {subline && (
              <p className="body-text text-cream/60 text-[13px] mt-3">{subline}</p>
            )}
          </div>
          <div className="flex-shrink-0">
            <Link
              href={buttonHref}
              className="group inline-flex items-center gap-3 bg-brass text-navy px-6 py-3.5 text-[13px] font-body font-semibold uppercase tracking-[0.08em] hover:bg-cream transition-colors"
            >
              {buttonLabel}
              <ArrowRight
                size={14}
                strokeWidth={2.5}
                className="transition-transform group-hover:translate-x-1"
              />
            </Link>
          </div>
        </div>
      </div>
    </section>
  );
}

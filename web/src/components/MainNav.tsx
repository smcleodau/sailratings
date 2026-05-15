import Link from "next/link";

const NAV_ITEMS = [
  { label: "Ratings", href: "/ratings" },
  { label: "Fleet Analysis", href: "/fleet" },
  { label: "Results", href: "/results" },
] as const;

function SailLogo({ className = "" }: { className?: string }) {
  return (
    <svg viewBox="0 0 28 28" fill="none" className={className} aria-hidden="true">
      <path d="M14 2 L14 26" stroke="currentColor" strokeWidth="1.5" />
      <path d="M14 4 L24 20 L14 20 Z" fill="currentColor" opacity="0.5" />
      <path d="M14 8 L6 20 L14 20 Z" fill="currentColor" opacity="0.3" />
    </svg>
  );
}

interface MainNavProps {
  /** "on-image" → white text for use over the hero image; "on-cream" → navy text for editorial pages. */
  theme: "on-image" | "on-cream";
  /** Optional CTA button on the right. Pass children to render it; omit to render nothing. */
  cta?: React.ReactNode;
}

export default function MainNav({ theme, cta }: MainNavProps) {
  const onImage = theme === "on-image";
  const wordmarkClass = onImage ? "text-white" : "text-navy";
  const linkBase = onImage
    ? "text-white/70 hover:text-white"
    : "text-charcoal/70 hover:text-navy";
  const containerClass = onImage
    ? "absolute top-0 left-0 w-full z-50"
    : "relative w-full z-30 border-b border-border-light bg-cream";

  return (
    <nav className={`${containerClass} px-8 sm:px-12 py-6 flex items-center justify-between`}>
      <Link href="/" className="flex items-center gap-2.5 group">
        <SailLogo className={`w-6 h-6 ${wordmarkClass} transition-opacity group-hover:opacity-80`} />
        <span className={`brand-wordmark ${wordmarkClass} transition-opacity group-hover:opacity-80`}>
          Sail Ratings
        </span>
      </Link>
      <div className="hidden md:flex items-center gap-10 text-[14px] font-body font-medium">
        {NAV_ITEMS.map((item) => (
          <Link
            key={item.href}
            href={item.href}
            className={`${linkBase} transition-colors`}
          >
            {item.label}
          </Link>
        ))}
      </div>
      {cta ?? <span className="hidden sm:block w-[1px]" aria-hidden="true" />}
    </nav>
  );
}

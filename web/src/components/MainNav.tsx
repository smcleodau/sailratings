import Link from "next/link";
import Image from "next/image";
import { SignInButton, SignUpButton, Show, UserButton } from '@clerk/nextjs';

const NAV_ITEMS = [
  { label: "Ratings", href: "/ratings" },
  { label: "Fleet Analysis", href: "/fleet" },
  { label: "Results", href: "/results" },
] as const;

interface MainNavProps {
  /** "on-image" → dark surface over hero; "on-cream" → paper surface for public pages. */
  theme: "on-image" | "on-cream";
  /** Optional CTA button on the right. Pass children to render it; omit to render nothing. */
  cta?: React.ReactNode;
}

export default function MainNav({ theme, cta }: MainNavProps) {
  const onImage = theme === "on-image";
  const linkBase = onImage
    ? "text-[var(--sr-text-secondary)] hover:text-[var(--sr-paper)]"
    : "text-[var(--sr-text-secondary)] hover:text-[var(--sr-ink)]";
  const containerClass = onImage
    ? "absolute top-0 left-0 w-full z-50 bg-[var(--sr-surface-page)]/80 backdrop-blur-md border-b border-[var(--sr-border-subtle)]"
    : "relative w-full z-30 border-b border-[var(--sr-border-subtle)] bg-[var(--sr-surface-card)]";

  return (
    <nav
      className={`${containerClass} px-8 sm:px-12 py-4 grid grid-cols-[1fr_auto_1fr] items-center gap-6`}
    >
      <Link href="/" className="flex items-center gap-2.5 group justify-self-start">
        <Image
          src="/brand/wordmark-outlined.svg"
          alt="SailRatings"
          width={154}
          height={23}
          priority
          className="h-5 w-auto"
        />
      </Link>
      <div className="hidden md:flex items-center justify-center gap-10 text-[13px] font-body font-medium uppercase tracking-wider">
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
      <div className="justify-self-end flex items-center gap-4">
        {cta}
        <Show when="signed-out">
          <SignInButton mode="modal">
            <button className={`text-xs font-semibold uppercase tracking-wider ${linkBase} transition-colors px-2 py-1`}>Sign In</button>
          </SignInButton>
          <SignUpButton mode="modal">
            <button className="sr-button sr-button--primary text-xs py-2 px-4">Sign Up</button>
          </SignUpButton>
        </Show>
        <Show when="signed-in">
          <UserButton />
        </Show>
      </div>
    </nav>
  );
}

import Image from "next/image";
import Link from "next/link";

interface AuthFrameProps {
  /** Supporting line under the wordmark, e.g. "Sign in to your account". */
  strapline: string;
  children: React.ReactNode;
}

/**
 * AUTH-01-02 — Shared frame for the /sign-in and /sign-up pages.
 *
 * Abyss ground with a soft marine light pool, corner-bracketed card on the
 * surface-card colour, and the SailRatings wordmark — the same first-screen
 * language as the public site (SPEC-09 §2.2: custom layout on the DS tokens,
 * Clerk component mounted inside).
 */
export default function AuthFrame({ strapline, children }: AuthFrameProps) {
  return (
    <main
      data-testid="auth-frame"
      className="relative min-h-screen flex items-center justify-center overflow-hidden bg-[var(--sr-abyss)] px-4 py-10"
    >
      {/* Ambient field — marine light pooling over the abyss, like a
          spreader light on night water. */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-0"
      >
        <div className="absolute -top-[20%] left-1/2 h-[55%] w-[80%] -translate-x-1/2 rounded-full bg-[var(--sr-marine-900)]/50 blur-[120px]" />
        <div className="absolute bottom-[-15%] right-[-10%] h-[45%] w-[45%] rounded-full bg-[var(--sr-marine-400)]/[0.07] blur-[110px]" />
      </div>

      <div className="relative z-10 w-full max-w-md">
        {/* Brand */}
        <div className="mb-8 flex flex-col items-center gap-4 text-center">
          <Link href="/" aria-label="SailRatings home" className="inline-block">
            <Image
              src="/brand/wordmark-outlined.svg"
              alt="SailRatings"
              width={220}
              height={33}
              priority
              className="h-7 w-auto"
            />
          </Link>
          <p className="sr-label !text-[var(--sr-text-label)]">{strapline}</p>
        </div>

        {/* Corner-bracketed card (see .sr-auth-card in sailratings.css) */}
        <div className="sr-auth-card">
          <span aria-hidden="true" className="sr-auth-corner sr-auth-corner--tl" />
          <span aria-hidden="true" className="sr-auth-corner sr-auth-corner--tr" />
          <span aria-hidden="true" className="sr-auth-corner sr-auth-corner--bl" />
          <span aria-hidden="true" className="sr-auth-corner sr-auth-corner--br" />
          <div className="sr-auth-card-inner">{children}</div>
        </div>
      </div>
    </main>
  );
}

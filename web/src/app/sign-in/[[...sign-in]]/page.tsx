"use client";

import { SignIn } from "@clerk/nextjs";
import { clerkAppearance } from "@/lib/clerkAppearance";

export default function SignInPage() {
  return (
    <div className="min-h-screen flex items-center justify-center relative overflow-hidden bg-navy font-body">
      {/* Ambient field — brass light pooling over deep navy, like lantern glow on water */}
      <div className="absolute inset-0 z-0 overflow-hidden pointer-events-none">
        <div className="absolute top-[-20%] left-[-10%] w-[60%] h-[60%] bg-navy-light/40 rounded-full blur-[120px] mix-blend-screen" />
        <div className="absolute bottom-[-10%] right-[-10%] w-[50%] h-[50%] bg-brass/10 rounded-full blur-[110px] mix-blend-screen" />
        <div className="absolute top-[30%] left-[50%] w-[40%] h-[40%] bg-brass/[0.06] rounded-full blur-[120px] mix-blend-screen" />
      </div>

      <div className="relative z-10 w-full max-w-md p-6 flex flex-col items-center">
        {/* Brand */}
        <div className="mb-8 flex flex-col items-center">
          <div className="w-12 h-12 bg-cream/[0.06] rounded-xl backdrop-blur-md border border-cream/15 flex items-center justify-center mb-4 shadow-[0_0_30px_rgba(194,155,97,0.18)]">
            <svg viewBox="0 0 28 28" fill="none" className="w-6 h-6 text-brass" aria-hidden="true">
              <path d="M14 2 L14 26" stroke="currentColor" strokeWidth="1.5" />
              <path d="M14 4 L24 20 L14 20 Z" fill="currentColor" opacity="0.5" />
              <path d="M14 8 L6 20 L14 20 Z" fill="currentColor" opacity="0.3" />
            </svg>
          </div>
          <h1 className="text-3xl font-extrabold tracking-tight text-cream mb-2 brand-wordmark uppercase">
            SAIL<span className="text-brass">RATINGS</span>
          </h1>
          <p className="text-brass/80 text-xs font-medium tracking-[0.18em] uppercase data-mono">
            Secure Admin Gateway
          </p>
        </div>

        <div className="w-full flex justify-center backdrop-blur-xl bg-cream/[0.02] p-1 rounded-2xl border border-cream/[0.06] shadow-2xl">
          <SignIn appearance={clerkAppearance} />
        </div>
      </div>
    </div>
  );
}

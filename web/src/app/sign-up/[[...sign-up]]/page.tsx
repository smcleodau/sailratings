"use client";

import { SignUp } from "@clerk/nextjs";
import Image from "next/image";

export default function SignUpPage() {
  const clerkAppearance: any = {
    layout: {
      socialButtonsVariant: "blockButton",
      socialButtonsPlacement: "top",
    },
    variables: {
      colorBackground: 'var(--sr-surface-card)',
      colorInputBackground: 'var(--sr-surface-deep)',
      colorInputText: 'var(--sr-paper)',
      colorPrimary: 'var(--sr-action)',
      colorText: 'var(--sr-text-primary)',
      colorTextSecondary: 'var(--sr-text-secondary)',
      colorTextOnPrimaryBackground: 'var(--sr-action-text)',
      colorNeutral: 'var(--sr-text-primary)',
      fontFamily: 'var(--sr-font-body)',
    },
    elements: {
      cardBox: "sr-card shadow-[var(--sr-shadow-floating)] border-[var(--sr-border-strong)] p-0 overflow-hidden",
      card: "!bg-[var(--sr-surface-card)] shadow-none w-full p-8",
      logoBox: "hidden",
      headerTitle: "!text-[var(--sr-paper)] font-extrabold text-xl uppercase font-display",
      headerSubtitle: "!text-[var(--sr-text-secondary)] font-normal text-sm mt-1",
      formButtonPrimary: "sr-button sr-button--primary w-full py-3 text-sm tracking-wider uppercase font-display",
      socialButtonsBlockButton: "!bg-[var(--sr-surface-deep)] hover:!bg-[var(--sr-surface-raised)] !border !border-[var(--sr-border-strong)] !text-[var(--sr-text-primary)] rounded-[var(--sr-radius-control)] transition-colors font-medium",
      socialButtonsBlockButtonText: "!text-[var(--sr-text-primary)] font-medium text-sm",
      socialButtonsIconButton: "!bg-[var(--sr-surface-deep)] hover:!bg-[var(--sr-surface-raised)] !border !border-[var(--sr-border-strong)] rounded-[var(--sr-radius-control)] transition-colors p-2.5",
      formFieldInput: "sr-input sr-data !bg-[var(--sr-surface-deep)] !border-[var(--sr-border-strong)] !text-[var(--sr-paper)] placeholder:!text-[var(--sr-text-tertiary)] focus:!border-[var(--sr-action)] text-sm transition-colors",
      formFieldLabel: "sr-label mb-1 block",
      footerActionLink: "!text-[var(--sr-link)] hover:!text-[var(--sr-link-hover)] font-medium text-sm",
      footerActionText: "!text-[var(--sr-text-secondary)] text-sm font-normal",
      footer: "!bg-[var(--sr-abyss)] !border-t !border-[var(--sr-border-subtle)] p-4 rounded-b-[var(--sr-radius-card)]",
      dividerLine: "!bg-[var(--sr-border-subtle)]",
      dividerText: "sr-label !text-[var(--sr-text-tertiary)]",
      identityPreviewText: "!text-[var(--sr-paper)] font-medium text-sm",
      identityPreviewEditButton: "!text-[var(--sr-link)] hover:!text-[var(--sr-link-hover)] font-medium text-sm"
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center relative overflow-hidden bg-[var(--sr-surface-page)] text-[var(--sr-text-primary)] font-body">
      {/* Background Polar Geometry Accent */}
      <svg
        width="600"
        height="600"
        viewBox="0 0 600 600"
        className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 opacity-20 pointer-events-none"
        aria-hidden="true"
      >
        <g fill="none" stroke="var(--sr-marine-600)">
          <circle cx="300" cy="300" r="90" strokeOpacity="0.5" />
          <circle cx="300" cy="300" r="170" strokeOpacity="0.4" />
          <circle cx="300" cy="300" r="250" strokeOpacity="0.3" />
          <circle cx="300" cy="300" r="330" strokeOpacity="0.2" />
          <line x1="300" y1="0" x2="300" y2="600" strokeOpacity="0.3" />
          <line x1="0" y1="300" x2="600" y2="300" strokeOpacity="0.3" />
          <circle cx="300" cy="300" r="210" stroke="var(--sr-marine-600)" strokeWidth="1.5" strokeOpacity="0.5" />
        </g>
      </svg>

      <div className="relative z-10 w-full max-w-md p-6 flex flex-col items-center">
        {/* Brand Header using Official Outlined Wordmark SVG */}
        <div className="mb-6 flex flex-col items-center text-center">
          <Image
            src="/brand/wordmark-outlined.svg"
            alt="SailRatings"
            width={186}
            height={28}
            priority
            className="h-7 w-auto"
          />
          <p className="sr-label mt-2.5 text-[11px] text-[var(--sr-text-label)]">
            Competitive Sailing Intelligence
          </p>
        </div>
        
        <div className="w-full">
          <SignUp appearance={clerkAppearance} />
        </div>

        <div className="mt-8 text-center">
          <p className="sr-label text-[10px] text-[var(--sr-text-tertiary)]">
            sailratings.com &bull; Admin Gateway
          </p>
        </div>
      </div>
    </div>
  );
}

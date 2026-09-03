import { dark } from "@clerk/themes";

/**
 * Clerk appearance themed to the SailRatings design system.
 *
 * Tokens (from src/styles/sailratings.css):
 *   abyss #07100f · surface-card #0e1817 · marine-900 #0a2a2c
 *   marine-200 #9cc7c2 · signal-500 #ff4119 · ink-on-dark #e9f0ee
 *   text-secondary #9db3ae · text-label #7e948f
 *
 * Auth surfaces sit on the Abyss ground with a Marine card, signal-coral
 * primary action, and paper type — the same palette the rest of the public
 * site uses, so the auth pages read as SailRatings pages, not a bolt-on.
 *
 * Used by ClerkProvider (so the modal <SignIn /> / <UserButton /> match) and
 * by the /sign-in and /sign-up pages.
 */
export const clerkAppearance = {
  baseTheme: dark,
  variables: {
    colorBackground: "#0e1817", // surface-card
    colorPrimary: "#ff4119", // signal-500 — action colour
    colorPrimaryText: "#07100f", // abyss text on signal
    colorText: "#e9f0ee", // ink-on-dark
    colorTextSecondary: "#9db3ae", // text-secondary
    colorNeutral: "rgba(255, 255, 255, 0.14)", // border-strong
    colorInputBackground: "rgba(255, 255, 255, 0.04)",
    colorInputText: "#e9f0ee",
    colorDanger: "#ff6b54",
    colorSuccess: "#3d9e6e", // starboard
    borderRadius: "0.75rem", // --sr-radius-control
    borderRadiusSmall: "0.375rem", // --sr-radius-small
    borderRadiusMedium: "0.75rem",
    borderRadiusLarge: "1rem", // --sr-radius-card
    fontFamily: '"Libre Franklin", system-ui, sans-serif', // --sr-font-body
  },
  elements: {
    rootBox: "w-full",
    card: "bg-transparent shadow-none w-full",
    cardBox: "shadow-none",
    logoBox: "hidden",
    headerTitle: "text-[#e9f0ee] font-display uppercase tracking-wide",
    headerSubtitle: "text-[#9db3ae]",
    socialButtonsBlockButton:
      "bg-white/[0.06] border border-white/[0.14] hover:bg-white/[0.12] text-[#e9f0ee] rounded-xl transition-all",
    socialButtonsBlockButtonText: "text-[#e9f0ee] font-medium",
    dividerLine: "bg-white/[0.08]",
    dividerText: "text-[#7e948f]",
    formFieldLabel: "text-[#9db3ae]",
    formFieldInput:
      "bg-white/[0.04] border border-white/[0.14] text-[#e9f0ee] rounded-xl focus:border-[#3e9b95] focus:ring-[#3e9b95]/20 transition-all",
    formFieldInputShowPasswordButton: "text-[#9db3ae] hover:text-[#e9f0ee]",
    otpCodeFieldInput:
      "bg-white/[0.04] border border-white/[0.14] text-[#e9f0ee] rounded-lg focus:border-[#3e9b95] transition-all",
    formButtonPrimary:
      "bg-[#ff4119] hover:bg-[#c92b12] text-[#07100f] hover:text-[#f3f1ec] rounded-full font-semibold transition-all",
    footerActionLink: "text-[#9cc7c2] hover:text-[#e6f0ee]",
    identityPreviewText: "text-[#e9f0ee]",
    identityPreviewEditButton: "text-[#9cc7c2] hover:text-[#e6f0ee]",
    profileSectionTitle: "text-[#e9f0ee] font-display uppercase tracking-wide",
    navbarButton: "text-[#9db3ae] hover:text-[#e9f0ee]",
    avatarBox: "border border-white/[0.14]",
    // Error states — surfaced inline under the offending field and as an
    // alert above the form when credentials are rejected.
    formFieldErrorText: "text-[#ff6b54]",
    formFieldWarningText: "text-[#e8b23a]",
    alert: "bg-[#ff4119]/10 border border-[#ff4119]/30 text-[#ffd9ce]",
    alertText: "text-[#ffd9ce]",
  },
};

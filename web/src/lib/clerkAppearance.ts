import { dark } from "@clerk/themes";

/**
 * Clerk appearance themed to the Sail Ratings brand.
 *
 * Palette (from tailwind.config.ts / globals.css):
 *   navy   #0A2240 · navy-light #163156
 *   brass  #C29B61 · brass-dark #A6834D
 *   cream  #F4F1E8 · charcoal #2C2C2C · border #D1C8B7
 *
 * Used by ClerkProvider (so the UserButton / <UserProfile /> match) and by the
 * sign-in / sign-up pages. Auth surfaces render on a navy field with a brass
 * accent and cream type, set in the Soehne typeface.
 */
export const clerkAppearance = {
  baseTheme: dark,
  variables: {
    colorBackground: "#163156", // navy-light panel
    colorPrimary: "#C29B61", // brass
    colorPrimaryText: "#0A2240", // navy on brass
    colorText: "#F4F1E8", // cream
    colorTextSecondary: "rgba(244, 241, 232, 0.72)",
    colorNeutral: "rgba(244, 241, 232, 0.12)",
    colorInputBackground: "rgba(244, 241, 232, 0.06)",
    colorInputText: "#F4F1E8",
    colorDanger: "#E06B5A",
    colorSuccess: "#7FB069",
    borderRadius: "0.75rem",
    borderRadiusSmall: "0.5rem",
    borderRadiusMedium: "0.75rem",
    borderRadiusLarge: "1rem",
  },
  elements: {
    rootBox: "w-full",
    card: "bg-transparent shadow-none w-full",
    cardBox: "shadow-none",
    logoBox: "hidden",
    headerTitle: "text-cream font-display",
    headerSubtitle: "text-cream/60",
    socialButtonsBlockButton:
      "bg-cream/10 border border-cream/15 hover:bg-cream/20 text-cream rounded-xl transition-all",
    socialButtonsBlockButtonText: "text-cream font-medium",
    dividerLine: "bg-cream/10",
    dividerText: "text-cream/40",
    formFieldLabel: "text-cream/80",
    formFieldInput:
      "bg-cream/5 border border-cream/10 text-cream rounded-xl focus:border-brass focus:ring-brass/20 transition-all",
    formFieldInputShowPasswordButton: "text-cream/60 hover:text-cream",
    otpCodeFieldInput:
      "bg-cream/5 border border-cream/10 text-cream rounded-lg focus:border-brass transition-all",
    formButtonPrimary:
      "bg-brass hover:bg-brass-dark text-navy rounded-full font-semibold transition-all shadow-[0_0_18px_rgba(194,155,97,0.28)]",
    footerActionLink: "text-brass hover:text-brass-dark",
    identityPreviewText: "text-cream",
    identityPreviewEditButton: "text-brass hover:text-brass-dark",
    profileSectionTitle: "text-cream font-display",
    navbarButton: "text-cream/80 hover:text-cream",
    avatarBox: "border border-cream/15",
  },
};

import type { Config } from "tailwindcss";

/**
 * SailingRatings Design Tokens
 *
 * Note: Tailwind v4 reads theme configuration from @theme in globals.css.
 * This file documents the design system for reference and tooling support.
 * The actual runtime values are defined in src/app/globals.css via @theme inline.
 */
const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        linen: "#FAF6F0",
        navy: {
          DEFAULT: "#0F1B2D",
          light: "#1A2A42",
        },
        copper: {
          DEFAULT: "#B87333",
          dark: "#9A5F28",
        },
        teal: {
          DEFAULT: "#2A7B88",
          light: "#3A9DAD",
        },
        ink: {
          DEFAULT: "#1C1C1C",
          light: "#3A3A3A",
        },
        border: {
          DEFAULT: "#D4CFC7",
          light: "#E8E3DB",
        },
        cream: "#F5F0E8",
        muted: "#7A756D",
      },
      fontFamily: {
        serif: ['"Cormorant Garamond"', "Georgia", "serif"],
        body: ['"Crimson Pro"', "Georgia", "serif"],
        mono: ['"Fira Code"', '"Courier New"', "monospace"],
      },
    },
  },
  plugins: [],
};

export default config;

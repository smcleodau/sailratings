import type { Config } from "tailwindcss";

/**
 * SailRatings Design Tokens
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
        navy: {
          DEFAULT: "#0B1623",
          light: "#142033",
        },
        sand: "#F7F3ED",
        copper: {
          DEFAULT: "#C27B3E",
          dark: "#A8692F",
        },
        signal: {
          DEFAULT: "#1B7FA3",
          light: "#2194BC",
        },
        ink: {
          DEFAULT: "#1A1A1A",
          light: "#3A3A3A",
        },
        border: {
          DEFAULT: "#DDD8CF",
          light: "#E8E3DB",
        },
        cream: "#F0EBE3",
        muted: "#8A8478",
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

import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        navy: { DEFAULT: "#0A2240", light: "#163156" },
        brass: { DEFAULT: "#C29B61", dark: "#A6834D" },
        cream: { DEFAULT: "#F4F1E8" },
        charcoal: { DEFAULT: "#2C2C2C", light: "#555555" },
        slate: "#6B7A8F",
        border: { DEFAULT: "#D1C8B7", light: "#E5DFD4" },
        muted: "#6B7A8F",
        sand: "#F4F1E8",
      },
      fontFamily: {
        display: ['"Soehne"', "system-ui", "sans-serif"],
        body: ['"Soehne"', "system-ui", "sans-serif"],
        mono: ['"Roboto Mono"', '"Courier New"', "monospace"],
      },
    },
  },
  plugins: [],
};

export default config;

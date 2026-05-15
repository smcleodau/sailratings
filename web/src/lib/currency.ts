interface CurrencyInfo {
  code: string;
  symbol: string;
  amount: number;
  display: string;
}

const CURRENCY_MAP: Record<string, CurrencyInfo> = {
  "en-GB": { code: "GBP", symbol: "\u00A3", amount: 79, display: "\u00A379" },
  "en-AU": { code: "AUD", symbol: "A$", amount: 149, display: "A$149" },
  "fr": { code: "EUR", symbol: "\u20AC", amount: 89, display: "\u20AC89" },
  "de": { code: "EUR", symbol: "\u20AC", amount: 89, display: "\u20AC89" },
  "es": { code: "EUR", symbol: "\u20AC", amount: 89, display: "\u20AC89" },
  "it": { code: "EUR", symbol: "\u20AC", amount: 89, display: "\u20AC89" },
  "nl": { code: "EUR", symbol: "\u20AC", amount: 89, display: "\u20AC89" },
  "pt": { code: "EUR", symbol: "\u20AC", amount: 89, display: "\u20AC89" },
};

const DEFAULT_CURRENCY: CurrencyInfo = {
  code: "USD",
  symbol: "$",
  amount: 99,
  display: "$99",
};

export function detectCurrency(): CurrencyInfo {
  if (typeof navigator === "undefined") return DEFAULT_CURRENCY;

  const lang = navigator.language;

  // Exact match first
  if (CURRENCY_MAP[lang]) return CURRENCY_MAP[lang];

  // Try base language (e.g. "en-GB" -> "en")
  const base = lang.split("-")[0];
  if (CURRENCY_MAP[base]) return CURRENCY_MAP[base];

  return DEFAULT_CURRENCY;
}

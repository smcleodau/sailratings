/**
 * Currency detection + display symbols only. The amount charged is never
 * computed here: the API resolves it from the Stripe catalogue by lookup_key
 * (report_gbp / report_usd / report_aud). `display` is a label for the button.
 */
interface CurrencyInfo {
  code: string;
  symbol: string;
  display: string;
}

// No EUR catalogue price exists (SPEC-23 §4): euro locales are charged the
// USD price, so they fall through to DEFAULT_CURRENCY rather than showing a
// euro label the checkout would not honour.
const CURRENCY_MAP: Record<string, CurrencyInfo> = {
  "en-GB": { code: "GBP", symbol: "\u00A3", display: "\u00A379" },
  "en-AU": { code: "AUD", symbol: "A$", display: "A$149" },
};

const DEFAULT_CURRENCY: CurrencyInfo = {
  code: "USD",
  symbol: "$",
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

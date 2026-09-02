"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import Image from "next/image";
import { Search, X } from "lucide-react";
import { searchBoatsFull, getStats, formatStatCount, type SearchResult, type SearchSuggestion, type StatsResponse } from "@/lib/api";
import MainNav from "@/components/MainNav";

const SYSTEMS = ["IRC", "ORC"];
const CYCLE_MS = 3500;

function Cycling() {
  const [i, setI] = useState(0);
  const [show, setShow] = useState(true);
  useEffect(() => {
    const t = setInterval(() => {
      setShow(false);
      setTimeout(() => { setI((p) => (p + 1) % SYSTEMS.length); setShow(true); }, 350);
    }, CYCLE_MS);
    return () => clearInterval(t);
  }, []);
  return (
    <span className="inline-block text-center" style={{
      width: "3ch",
      opacity: show ? 1 : 0, transform: show ? "translateY(0)" : "translateY(4px)",
      transition: "opacity 0.35s ease, transform 0.35s ease",
    }}>{SYSTEMS[i]}</span>
  );
}

const FLAGS: Record<string, string> = {
  GBR:"GB",FRA:"FR",IRL:"IE",NED:"NL",GER:"DE",ITA:"IT",ESP:"ES",AUS:"AU",
  NZL:"NZ",USA:"US",CAN:"CA",SWE:"SE",NOR:"NO",DEN:"DK",FIN:"FI",BEL:"BE",
  POR:"PT",GRE:"GR",CRO:"HR",SUI:"CH",AUT:"AT",POL:"PL",HKG:"HK",JPN:"JP",
  RSA:"ZA",BRA:"BR",ARG:"AR",CHI:"CL",MEX:"MX",TUR:"TR",
};

function Flag({ country }: { country: string }) {
  const iso = FLAGS[country?.toUpperCase()] || country?.slice(0, 2) || "";
  if (!iso) return null;
  const flag = [...iso.toUpperCase()].map((c) => String.fromCodePoint(0x1f1e6 + c.charCodeAt(0) - 65)).join("");
  return <span className="text-sm">{flag}</span>;
}

interface HeroProps { onBoatSelected: (boat: SearchResult) => void; }

export default function Hero({ onBoatSelected }: HeroProps) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [suggestions, setSuggestions] = useState<SearchSuggestion[]>([]);
  // OPS-02-11: marketing numbers come from GET /v1/stats (DB census), not copy.
  const [stats, setStats] = useState<StatsResponse | null>(null);
  const [searchedQuery, setSearchedQuery] = useState("");
  const [isOpen, setIsOpen] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [highlight, setHighlight] = useState(-1);
  const inputRef = useRef<HTMLInputElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout>>(undefined);
  const resultsRef = useRef<SearchResult[]>([]);
  const suggestionsRef = useRef<SearchSuggestion[]>([]);

  const doSearch = useCallback(async (q: string) => {
    if (q.trim().length < 2) {
      setResults([]); resultsRef.current = [];
      setSuggestions([]); suggestionsRef.current = [];
      setIsOpen(false); setSearchedQuery(""); return;
    }
    setIsLoading(true);
    try {
      const data = await searchBoatsFull(q);
      setResults(data.results); resultsRef.current = data.results;
      setSuggestions(data.suggestions ?? []); suggestionsRef.current = data.suggestions ?? [];
      setSearchedQuery(q.trim());
      setIsOpen(data.results.length > 0 || (data.suggestions?.length ?? 0) > 0);
      setHighlight(-1);
    } catch {
      setResults([]); resultsRef.current = [];
      setSuggestions([]); suggestionsRef.current = [];
      setIsOpen(false);
    }
    finally { setIsLoading(false); }
  }, []);

  const handleChange = (v: string) => { setQuery(v); clearTimeout(debounceRef.current); debounceRef.current = setTimeout(() => doSearch(v), 250); };
  const handleSelect = (boat: SearchResult | SearchSuggestion) => {
    setQuery(boat.boat_name); setIsOpen(false);
    setResults([]); resultsRef.current = [];
    setSuggestions([]); suggestionsRef.current = [];
    onBoatSelected({ ...boat, score: 0 } as SearchResult);
  };
  // Submit the search: pick the first result (or first suggestion) and navigate to it.
  // Wired to the visible "Search" button + form submit + Enter key.
  const handleSubmit = async (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    clearTimeout(debounceRef.current);
    if (query.trim().length < 2) return;
    if (resultsRef.current.length === 0 && suggestionsRef.current.length === 0) {
      await doSearch(query);
    }
    const first = resultsRef.current[0] ?? suggestionsRef.current[0];
    if (first) handleSelect(first);
  };
  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") {
      e.preventDefault();
      if (isOpen && highlight >= 0 && results[highlight]) {
        handleSelect(results[highlight]);
      } else {
        void handleSubmit();
      }
      return;
    }
    if (!isOpen || !results.length) return;
    if (e.key === "ArrowDown") { e.preventDefault(); setHighlight((p) => (p < results.length - 1 ? p + 1 : 0)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setHighlight((p) => (p > 0 ? p - 1 : results.length - 1)); }
    else if (e.key === "Escape") setIsOpen(false);
  };

  useEffect(() => {
    const handler = (e: MouseEvent) => { if (containerRef.current && !containerRef.current.contains(e.target as Node)) setIsOpen(false); };
    document.addEventListener("mousedown", handler); return () => document.removeEventListener("mousedown", handler);
  }, []);
  useEffect(() => { return () => clearTimeout(debounceRef.current); }, []);

  // Live census numbers; if the stats endpoint is unreachable we keep the
  // last-published fallback copy below rather than render nothing.
  useEffect(() => {
    let cancelled = false;
    getStats()
      .then((s) => { if (!cancelled) setStats(s); })
      .catch(() => { /* keep fallback copy */ });
    return () => { cancelled = true; };
  }, []);

  const raceResultsCount = stats ? formatStatCount(stats.race_results) : "31,000";
  const boatsCount = stats ? formatStatCount(stats.boats) : null;

  return (
    <section className="relative h-screen min-h-[600px] overflow-hidden">
      {/* Hero image — fills the entire section */}
      <div className="hero-image-wrap">
        <Image
          src="/hero-painting.png"
          alt="Racing yacht fleet at golden hour"
          fill
          priority
          className="object-cover object-center"
          sizes="100vw"
        />
      </div>

      <MainNav theme="on-image" />

      {/* Content — positioned in upper area, below nav */}
      <div className="relative z-20 flex flex-col items-center pt-[140px] px-6 text-center">
        <h1
          className="heading-display text-white animate-in delay-2"
          style={{
            fontSize: "clamp(2.2rem, 5vw, 4rem)",
            maxWidth: "18ch",
          }}
        >
          The <Cycling /> analysis your competitors wish they had
        </h1>

        <p
          className="body-text text-white/85 mt-5 max-w-lg leading-relaxed animate-in delay-3"
          style={{
            fontSize: "clamp(0.875rem, 1.2vw, 1.05rem)",
          }}
        >
          We analyze over {raceResultsCount} race results and every certificate ever published to find where your points are hiding.
        </p>

        {/* Search bar — solid cream card, dropdown-only interaction (no separate Search button) */}
        <div ref={containerRef} className="relative w-full max-w-xl mt-8 animate-in delay-4">
          <div
            className={`relative bg-cream/95 backdrop-blur-sm shadow-[0_20px_60px_-12px_rgba(0,0,0,0.45)] ring-1 ring-charcoal/10 transition-all duration-200 focus-within:ring-2 focus-within:ring-brass/70 focus-within:shadow-[0_24px_72px_-12px_rgba(0,0,0,0.55)] ${
              isOpen && (results.length > 0 || suggestions.length > 0)
                ? "rounded-t-md"
                : "rounded-md"
            }`}
          >
            <Search
              className="absolute left-5 top-1/2 -translate-y-1/2 text-charcoal/40"
              size={18}
              strokeWidth={1.5}
            />
            <input
              ref={inputRef}
              id="main-search"
              type="text"
              value={query}
              onChange={(e) => handleChange(e.target.value)}
              onKeyDown={handleKeyDown}
              onFocus={() => { if (results.length > 0 || suggestions.length > 0) setIsOpen(true); }}
              placeholder="Boat name, sail number, or design"
              className="w-full h-14 pl-12 pr-36 bg-transparent text-charcoal text-[15px] font-body placeholder:text-charcoal/40 focus:outline-none rounded-md"
              autoComplete="off"
              role="combobox"
              aria-expanded={isOpen}
            />

            {/* Right cluster: spinner / clear / Search button */}
            <div className="absolute right-2 top-1/2 -translate-y-1/2 flex items-center gap-1">
              {isLoading && (
                <div
                  className="w-4 h-4 mr-1 border-2 border-charcoal/15 border-t-brass animate-spin rounded-full"
                  aria-label="searching"
                />
              )}
              {!isLoading && query && (
                <button
                  type="button"
                  onClick={() => {
                    setQuery("");
                    setResults([]);
                    resultsRef.current = [];
                    setSuggestions([]);
                    suggestionsRef.current = [];
                    setIsOpen(false);
                    setSearchedQuery("");
                    inputRef.current?.focus();
                  }}
                  className="w-7 h-7 inline-flex items-center justify-center text-charcoal/35 hover:text-charcoal hover:bg-charcoal/5 rounded-full transition-colors"
                  aria-label="Clear search"
                >
                  <X size={14} strokeWidth={1.5} />
                </button>
              )}
              <button
                type="button"
                onClick={() => handleSubmit()}
                disabled={query.trim().length < 2}
                className="h-11 px-5 inline-flex items-center justify-center bg-brass text-white text-[13px] font-body font-semibold tracking-[0.04em] rounded-md transition-all hover:bg-brass-dark active:translate-y-px disabled:bg-charcoal/10 disabled:text-charcoal/35 disabled:cursor-not-allowed shadow-[0_2px_10px_-3px_rgba(166,131,77,0.55)] disabled:shadow-none"
                aria-label="Search boats"
              >
                Search
              </button>
            </div>
          </div>

          {/* Empty state — search returned nothing AND no suggestions */}
          {!isLoading && searchedQuery && searchedQuery === query.trim() && results.length === 0 && suggestions.length === 0 && (
            <p id="search-empty-state" className="mt-3 text-[13px] text-white/85 font-body text-center">
              No boats matching <span className="data-mono">{searchedQuery}</span>. Try a name or different sail number.
            </p>
          )}

          {/* Dropdown — flush to the input below, same cream card, no separate float */}
          {isOpen && (results.length > 0 || suggestions.length > 0) && (
            <div
              id="search-results-wrap"
              className="absolute z-50 left-0 right-0 bg-cream/95 backdrop-blur-sm rounded-b-md ring-1 ring-charcoal/10 shadow-[0_28px_72px_-12px_rgba(0,0,0,0.5)] max-h-96 overflow-y-auto border-t border-charcoal/10"
            >
              <div className="px-4 py-2 flex items-baseline justify-between border-b border-charcoal/10">
                <span className="text-[10px] uppercase tracking-[0.12em] text-charcoal/50 font-body font-medium">
                  {results.length > 0
                    ? `${results.length} match${results.length === 1 ? "" : "es"}`
                    : "Did you mean…"}
                </span>
                <span className="data-mono text-[10px] uppercase tracking-wider text-charcoal/30">
                  ↑↓ to navigate · ↵ to open
                </span>
              </div>
              <ul id="search-results">
                {results.map((boat, idx) => (
                  <li
                    key={boat.id}
                    onClick={() => handleSelect(boat)}
                    onMouseEnter={() => setHighlight(idx)}
                    className={`flex items-center gap-3 px-4 py-3 cursor-pointer border-b border-charcoal/5 last:border-0 transition-colors ${
                      highlight === idx ? "bg-brass/10" : "hover:bg-brass/5"
                    }`}
                  >
                    <span className="w-5 text-center flex-shrink-0">{boat.country ? <Flag country={boat.country} /> : null}</span>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-baseline gap-2 flex-nowrap overflow-hidden">
                        <span className="font-body font-bold text-sm text-charcoal truncate">{boat.boat_name}</span>
                        <span className="data-mono text-[11px] text-charcoal/55 flex-shrink-0">{boat.sail_number}</span>
                        {boat.design && <span className="text-[12px] text-charcoal/55 flex-shrink-0">&middot; {boat.design}</span>}
                      </div>
                    </div>
                  </li>
                ))}
                {suggestions.map((boat) => (
                  <li
                    key={`sug-${boat.id}`}
                    onClick={() => handleSelect(boat)}
                    className="flex items-center gap-3 px-4 py-3 cursor-pointer border-b border-charcoal/5 last:border-0 transition-colors hover:bg-brass/5"
                  >
                    <span className="w-5 text-center flex-shrink-0">{boat.country ? <Flag country={boat.country} /> : null}</span>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-baseline gap-2 flex-nowrap overflow-hidden">
                        <span className="font-body font-bold text-sm text-charcoal truncate">{boat.boat_name}</span>
                        <span className="data-mono text-[11px] text-charcoal/55 flex-shrink-0">{boat.sail_number}</span>
                        {boat.design && <span className="text-[12px] text-charcoal/55 flex-shrink-0">&middot; {boat.design}</span>}
                      </div>
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      </div>

      {/* Trust stats — white, at the very bottom, no fade */}
      <div className="absolute bottom-8 left-0 right-0 z-20 text-center animate-in delay-5">
        <p className="text-[14px] text-white/80 font-body font-semibold tracking-wider">
          {raceResultsCount}+ race results analyzed{boatsCount ? <> &middot; {boatsCount}+ boats</> : null} &middot; 14 years of data &middot; Trusted by teams big and small
        </p>
      </div>
    </section>
  );
}

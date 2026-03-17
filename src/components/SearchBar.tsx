"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import { Search, X } from "lucide-react";
import { searchBoats, type SearchResult } from "@/lib/api";

interface SearchBarProps {
  onBoatSelected: (boat: SearchResult) => void;
}

const COUNTRY_FLAGS: Record<string, string> = {
  GBR: "GB",
  FRA: "FR",
  IRL: "IE",
  NED: "NL",
  GER: "DE",
  ITA: "IT",
  ESP: "ES",
  AUS: "AU",
  NZL: "NZ",
  USA: "US",
  CAN: "CA",
  SWE: "SE",
  NOR: "NO",
  DEN: "DK",
  FIN: "FI",
  BEL: "BE",
  POR: "PT",
  GRE: "GR",
  CRO: "HR",
  SUI: "CH",
  AUT: "AT",
  POL: "PL",
  HKG: "HK",
  JPN: "JP",
  RSA: "ZA",
  BRA: "BR",
  ARG: "AR",
  CHI: "CL",
  MEX: "MX",
  RUS: "RU",
  TUR: "TR",
  ISR: "IL",
  SIN: "SG",
  MAS: "MY",
  THA: "TH",
  CHN: "CN",
};

function getCountryISO(country: string): string {
  if (!country) return "";
  const upper = country.toUpperCase();
  return COUNTRY_FLAGS[upper] || upper.slice(0, 2);
}

function CountryFlag({ country }: { country: string }) {
  const iso = getCountryISO(country);
  if (!iso) return null;

  // Use regional indicator symbols to render flag emoji
  const flag = [...iso.toUpperCase()]
    .map((c) => String.fromCodePoint(0x1f1e6 + c.charCodeAt(0) - 65))
    .join("");

  return (
    <span className="text-base leading-none" role="img" aria-label={country}>
      {flag}
    </span>
  );
}

export default function SearchBar({ onBoatSelected }: SearchBarProps) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [isOpen, setIsOpen] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [highlightIndex, setHighlightIndex] = useState(-1);
  const [error, setError] = useState<string | null>(null);

  const inputRef = useRef<HTMLInputElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout>>(undefined);
  const abortRef = useRef<AbortController>(undefined);

  // Debounced search
  const doSearch = useCallback(async (q: string) => {
    if (q.trim().length < 2) {
      setResults([]);
      setIsOpen(false);
      return;
    }

    // Cancel previous in-flight request
    abortRef.current?.abort();
    abortRef.current = new AbortController();

    setIsLoading(true);
    setError(null);

    try {
      const data = await searchBoats(q);
      setResults(data);
      setIsOpen(data.length > 0);
      setHighlightIndex(-1);
    } catch (err) {
      if (err instanceof Error && err.name !== "AbortError") {
        setError("Search failed. Please try again.");
        setResults([]);
        setIsOpen(false);
      }
    } finally {
      setIsLoading(false);
    }
  }, []);

  const handleChange = (value: string) => {
    setQuery(value);
    clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => doSearch(value), 250);
  };

  const handleSelect = (boat: SearchResult) => {
    setQuery(boat.boat_name);
    setIsOpen(false);
    setResults([]);
    onBoatSelected(boat);
  };

  const handleClear = () => {
    setQuery("");
    setResults([]);
    setIsOpen(false);
    setError(null);
    inputRef.current?.focus();
  };

  // Keyboard navigation
  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (!isOpen || results.length === 0) return;

    switch (e.key) {
      case "ArrowDown":
        e.preventDefault();
        setHighlightIndex((prev) =>
          prev < results.length - 1 ? prev + 1 : 0
        );
        break;
      case "ArrowUp":
        e.preventDefault();
        setHighlightIndex((prev) =>
          prev > 0 ? prev - 1 : results.length - 1
        );
        break;
      case "Enter":
        e.preventDefault();
        if (highlightIndex >= 0 && highlightIndex < results.length) {
          handleSelect(results[highlightIndex]);
        }
        break;
      case "Escape":
        setIsOpen(false);
        break;
    }
  };

  // Click outside to close
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (
        containerRef.current &&
        !containerRef.current.contains(e.target as Node)
      ) {
        setIsOpen(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  // Cleanup
  useEffect(() => {
    return () => {
      clearTimeout(debounceRef.current);
      abortRef.current?.abort();
    };
  }, []);

  return (
    <div ref={containerRef} className="relative w-full max-w-2xl mx-auto">
      {/* Search input */}
      <div className="relative">
        <Search
          className="absolute left-5 top-1/2 -translate-y-1/2 text-muted"
          size={22}
          strokeWidth={1.5}
        />
        <input
          ref={inputRef}
          type="text"
          value={query}
          onChange={(e) => handleChange(e.target.value)}
          onKeyDown={handleKeyDown}
          onFocus={() => {
            if (results.length > 0) setIsOpen(true);
          }}
          placeholder="Enter boat name or sail number..."
          id="main-search"
          className="w-full h-14 sm:h-16 pl-14 pr-14 bg-white border border-border text-charcoal text-[15px] sm:text-base font-body placeholder:text-slate/40 rounded-full shadow-sm transition-all hover:border-amber/30 focus:border-amber focus:shadow-md"
          autoComplete="off"
          role="combobox"
          aria-expanded={isOpen}
          aria-haspopup="listbox"
          aria-controls="search-results"
        />
        {query && (
          <button
            onClick={handleClear}
            className="absolute right-5 top-1/2 -translate-y-1/2 text-muted hover:text-charcoal transition-colors"
            aria-label="Clear search"
          >
            <X size={20} strokeWidth={1.5} />
          </button>
        )}
        {isLoading && (
          <div className="absolute right-14 top-1/2 -translate-y-1/2">
            <div
              className="w-4 h-4 border border-border border-t-amber animate-spin"
              style={{ borderRadius: "50%" }}
            />
          </div>
        )}
      </div>

      {/* Hint text */}
      {!isOpen && !error && results.length === 0 && !query && (
        <p className="mt-3 text-center text-sm text-muted/60 font-body italic">
          Try &ldquo;Chilli Pepper&rdquo;, &ldquo;GBR1663R&rdquo;, or &ldquo;Foggy Dew&rdquo;
        </p>
      )}

      {/* Error */}
      {error && (
        <p className="mt-2 text-sm text-teak font-body">{error}</p>
      )}

      {/* Dropdown */}
      {isOpen && results.length > 0 && (
        <ul
          id="search-results"
          role="listbox"
          className="absolute z-50 w-full mt-px bg-white border border-border border-t-0 max-h-80 overflow-y-auto"
          style={{ borderRadius: "0 0 1px 1px" }}
        >
          {results.map((boat, index) => (
            <li
              key={boat.id}
              role="option"
              aria-selected={highlightIndex === index}
              onClick={() => handleSelect(boat)}
              onMouseEnter={() => setHighlightIndex(index)}
              className={`flex items-center gap-4 px-5 py-3.5 cursor-pointer transition-colors border-b border-border-light last:border-b-0 ${
                highlightIndex === index ? "bg-sand" : "hover:bg-sand/50"
              }`}
            >
              {boat.country && <CountryFlag country={boat.country} />}
              <div className="flex-1 min-w-0">
                <div className="flex items-baseline gap-2">
                  <span className="heading-display text-lg text-charcoal truncate">
                    {boat.boat_name}
                  </span>
                  <span className="data-mono text-xs text-muted">
                    {boat.sail_number}
                  </span>
                </div>
                {boat.design && (
                  <span className="body-text text-sm text-muted">
                    {boat.design}
                  </span>
                )}
              </div>
              {boat.country && (
                <span className="data-mono text-xs text-muted whitespace-nowrap">
                  {boat.country}
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

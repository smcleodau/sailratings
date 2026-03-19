"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import Image from "next/image";
import { Search, X } from "lucide-react";
import { searchBoats, type SearchResult } from "@/lib/api";

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

function SailLogo({ className = "" }: { className?: string }) {
  return (
    <svg viewBox="0 0 28 28" fill="none" className={className} aria-hidden="true">
      <path d="M14 2 L14 26" stroke="currentColor" strokeWidth="1.5" />
      <path d="M14 4 L24 20 L14 20 Z" fill="currentColor" opacity="0.5" />
      <path d="M14 8 L6 20 L14 20 Z" fill="currentColor" opacity="0.3" />
    </svg>
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
  const [isOpen, setIsOpen] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [highlight, setHighlight] = useState(-1);
  const inputRef = useRef<HTMLInputElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout>>(undefined);

  const doSearch = useCallback(async (q: string) => {
    if (q.trim().length < 2) { setResults([]); setIsOpen(false); return; }
    setIsLoading(true);
    try { const data = await searchBoats(q); setResults(data); setIsOpen(data.length > 0); setHighlight(-1); }
    catch { setResults([]); setIsOpen(false); }
    finally { setIsLoading(false); }
  }, []);

  const handleChange = (v: string) => { setQuery(v); clearTimeout(debounceRef.current); debounceRef.current = setTimeout(() => doSearch(v), 250); };
  const handleSelect = (boat: SearchResult) => { setQuery(boat.boat_name); setIsOpen(false); setResults([]); onBoatSelected(boat); };
  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (!isOpen || !results.length) return;
    if (e.key === "ArrowDown") { e.preventDefault(); setHighlight((p) => (p < results.length - 1 ? p + 1 : 0)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setHighlight((p) => (p > 0 ? p - 1 : results.length - 1)); }
    else if (e.key === "Enter" && highlight >= 0) { e.preventDefault(); handleSelect(results[highlight]); }
    else if (e.key === "Escape") setIsOpen(false);
  };

  useEffect(() => {
    const handler = (e: MouseEvent) => { if (containerRef.current && !containerRef.current.contains(e.target as Node)) setIsOpen(false); };
    document.addEventListener("mousedown", handler); return () => document.removeEventListener("mousedown", handler);
  }, []);
  useEffect(() => { return () => clearTimeout(debounceRef.current); }, []);

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

      {/* Nav — sits ON TOP of the image, transparent background */}
      <nav className="absolute top-0 left-0 w-full z-50 px-8 sm:px-12 py-6 flex items-center justify-between">
        <div className="flex items-center gap-2.5">
          <SailLogo className="w-6 h-6 text-white" />
          <span className="brand-wordmark text-white">Sail Ratings</span>
        </div>
        <div className="hidden md:flex items-center gap-10 text-[14px] text-white/70 font-body font-medium">
          <span className="hover:text-white transition-colors cursor-default">Ratings</span>
          <span className="hover:text-white transition-colors cursor-default">Fleet Analysis</span>
          <span className="hover:text-white transition-colors cursor-default">Results</span>
          <span className="hover:text-white transition-colors cursor-default">About</span>
        </div>
        <button className="hidden sm:block text-white text-[13px] font-body font-semibold px-5 py-2.5 rounded hover:opacity-90 transition-opacity" style={{ background: "#F2542D" }}>
          Start Reviewing &rarr;
        </button>
      </nav>

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
          We analyze over 31,000 race results and every certificate ever published to find where your points are hiding.
        </p>

        {/* Search bar */}
        <div ref={containerRef} className="relative w-full max-w-lg mt-8 animate-in delay-4">
          <div className="relative">
            <Search className="absolute left-4 top-1/2 -translate-y-1/2 text-white/40" size={17} strokeWidth={1.5} />
            <input
              ref={inputRef}
              type="text"
              value={query}
              onChange={(e) => handleChange(e.target.value)}
              onKeyDown={handleKeyDown}
              onFocus={() => { if (results.length > 0) setIsOpen(true); }}
              placeholder="Enter your boat name or sail number..."
              className="w-full h-13 pl-11 pr-24 bg-white/15 backdrop-blur-md text-white text-[14px] font-body placeholder:text-white/50 rounded-lg border border-white/30 focus:outline-none focus:border-white/60 focus:bg-white/20 transition-all"
              autoComplete="off"
              role="combobox"
              aria-expanded={isOpen}
            />
            {query && !isLoading && (
              <button onClick={() => { setQuery(""); setResults([]); setIsOpen(false); inputRef.current?.focus(); }}
                className="absolute right-[88px] top-1/2 -translate-y-1/2 text-white/30 hover:text-white">
                <X size={15} strokeWidth={1.5} />
              </button>
            )}
            {isLoading && (
              <div className="absolute right-[88px] top-1/2 -translate-y-1/2">
                <div className="w-4 h-4 border-2 border-white/20 border-t-white animate-spin rounded-full" />
              </div>
            )}
            <button className="absolute right-1.5 top-1/2 -translate-y-1/2 bg-charcoal text-white text-[13px] font-body font-semibold px-6 py-2 rounded hover:bg-black transition-colors">
              Search
            </button>
          </div>

          {/* Dropdown */}
          {isOpen && results.length > 0 && (
            <ul className="absolute z-50 w-full mt-2 bg-white border border-border rounded-xl shadow-xl max-h-72 overflow-y-auto">
              {results.map((boat, idx) => (
                <li key={boat.id} onClick={() => handleSelect(boat)} onMouseEnter={() => setHighlight(idx)}
                  className={`flex items-center gap-3 px-4 py-3 cursor-pointer border-b border-border-light last:border-0 transition-colors ${highlight === idx ? "bg-cream" : "hover:bg-cream/50"}`}>
                  <span className="w-5 text-center flex-shrink-0">{boat.country ? <Flag country={boat.country} /> : null}</span>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-baseline gap-2 flex-nowrap overflow-hidden">
                      <span className="font-body font-bold text-sm text-charcoal truncate">{boat.boat_name}</span>
                      <span className="data-mono text-[11px] text-muted flex-shrink-0">{boat.sail_number}</span>
                      {boat.design && <span className="text-[12px] text-muted flex-shrink-0">&middot; {boat.design}</span>}
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      {/* Trust stats — white, at the very bottom, no fade */}
      <div className="absolute bottom-8 left-0 right-0 z-20 text-center animate-in delay-5">
        <p className="text-[14px] text-white/80 font-body font-semibold tracking-wider">
          31,000+ race results analyzed &middot; 14 years of data &middot; Trusted by teams big and small
        </p>
      </div>
    </section>
  );
}

"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import { Loader2 } from "lucide-react";
import Hero from "@/components/Hero";
import TeaserAnalysis from "@/components/TeaserAnalysis";
import { getBoat, type SearchResult, type BoatDetail } from "@/lib/api";

export default function Home() {
  const [selectedBoat, setSelectedBoat] = useState<SearchResult | null>(null);
  const [boatDetail, setBoatDetail] = useState<BoatDetail | null>(null);
  const [boatError, setBoatError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [, setTeaserText] = useState("");
  const resultsRef = useRef<HTMLDivElement>(null);

  const handleBoatSelected = (boat: SearchResult) => {
    setSelectedBoat(boat);
    setBoatDetail(null);
    setBoatError(null);
    setTeaserText("");
    setSearchQuery(boat.boat_name);
  };

  const handleTeaserComplete = useCallback((text: string) => {
    setTeaserText(text);
  }, []);

  // Fetch boat detail in page.tsx (was BoatCard's job)
  useEffect(() => {
    if (!selectedBoat) return;
    let cancelled = false;
    setBoatDetail(null);
    setBoatError(null);
    (async () => {
      try {
        const data = await getBoat(selectedBoat.id);
        if (!cancelled) setBoatDetail(data);
      } catch {
        if (!cancelled) setBoatError("Failed to load boat details.");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [selectedBoat]);

  useEffect(() => {
    if (selectedBoat && resultsRef.current) {
      setTimeout(() => {
        resultsRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
      }, 100);
    }
  }, [selectedBoat]);

  return (
    <main className="min-h-screen bg-cream">
      <Hero onBoatSelected={handleBoatSelected} />

      {selectedBoat && (
        <section ref={resultsRef} className="px-6 py-14 sm:py-20">
          {!boatDetail && !boatError && (
            <div className="max-w-3xl mx-auto flex items-center gap-3 py-12">
              <Loader2 size={16} className="animate-spin text-brass" />
              <span className="body-text text-charcoal/60 text-sm">
                Pulling {selectedBoat.boat_name}&rsquo;s file…
              </span>
            </div>
          )}
          {boatError && (
            <div className="max-w-3xl mx-auto py-12">
              <p className="body-text text-brass text-sm">{boatError}</p>
            </div>
          )}
          {boatDetail && (
            <TeaserAnalysis
              key={`teaser-${selectedBoat.id}`}
              boat={boatDetail}
              searchQuery={searchQuery}
              onComplete={handleTeaserComplete}
            />
          )}
        </section>
      )}

      <footer className="border-t border-border px-6 py-8 bg-cream">
        <div className="max-w-2xl mx-auto flex flex-col sm:flex-row items-center justify-between gap-3 text-[11px] text-muted">
          <span className="brand-wordmark text-muted/40">Sail Ratings</span>
          <span className="body-text text-center">
            Rating data sourced from public certificates. Not affiliated with the RORC Rating Office or ORC.
          </span>
        </div>
      </footer>
    </main>
  );
}

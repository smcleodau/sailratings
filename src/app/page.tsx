"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import Hero from "@/components/Hero";
import BoatCard from "@/components/BoatCard";
import TeaserAnalysis from "@/components/TeaserAnalysis";
import PurchaseCTA from "@/components/PurchaseCTA";
import type { SearchResult, BoatDetail } from "@/lib/api";

export default function Home() {
  const [selectedBoat, setSelectedBoat] = useState<SearchResult | null>(null);
  const [boatLoaded, setBoatLoaded] = useState(false);
  const [boatDetail, setBoatDetail] = useState<BoatDetail | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [teaserText, setTeaserText] = useState("");
  const resultsRef = useRef<HTMLDivElement>(null);

  const handleBoatSelected = (boat: SearchResult) => {
    setSelectedBoat(boat);
    setBoatLoaded(false);
    setBoatDetail(null);
    setTeaserText("");
    setSearchQuery(boat.boat_name);
  };

  const handleBoatLoaded = (boat: BoatDetail) => {
    setBoatDetail(boat);
    setBoatLoaded(true);
  };

  const handleTeaserComplete = useCallback((text: string) => {
    setTeaserText(text);
  }, []);

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

      {/* Results — on cream background */}
      {selectedBoat && (
        <section ref={resultsRef} className="px-6 py-16 space-y-10">
          <BoatCard
            key={selectedBoat.id}
            boatId={selectedBoat.id}
            boatName={selectedBoat.boat_name}
            onBoatLoaded={handleBoatLoaded}
          />
          {boatLoaded && boatDetail && (
            <TeaserAnalysis
              key={`teaser-${selectedBoat.id}`}
              boatId={selectedBoat.id}
              boatName={selectedBoat.boat_name}
              onComplete={handleTeaserComplete}
            />
          )}
          {boatLoaded && boatDetail && (
            <div className="section-divider" aria-hidden="true"><span className="diamond" /></div>
          )}
          {boatLoaded && boatDetail && (
            <PurchaseCTA
              boatId={selectedBoat.id}
              boatName={selectedBoat.boat_name}
              searchQuery={searchQuery}
              teaserText={teaserText}
            />
          )}
        </section>
      )}

      <footer className="border-t border-border px-6 py-8">
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

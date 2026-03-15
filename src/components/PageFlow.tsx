"use client";

import { useState, useRef, useEffect } from "react";
import SearchBar from "@/components/SearchBar";
import BoatCard from "@/components/BoatCard";
import TeaserAnalysis from "@/components/TeaserAnalysis";
import PurchaseCTA from "@/components/PurchaseCTA";
import type { SearchResult, BoatDetail } from "@/lib/api";

export default function PageFlow() {
  const [selectedBoat, setSelectedBoat] = useState<SearchResult | null>(null);
  const [boatLoaded, setBoatLoaded] = useState(false);
  const [boatDetail, setBoatDetail] = useState<BoatDetail | null>(null);

  const resultsRef = useRef<HTMLDivElement>(null);

  const handleBoatSelected = (boat: SearchResult) => {
    setSelectedBoat(boat);
    setBoatLoaded(false);
    setBoatDetail(null);
  };

  const handleBoatLoaded = (boat: BoatDetail) => {
    setBoatDetail(boat);
    setBoatLoaded(true);
  };

  // Scroll to results when a boat is selected
  useEffect(() => {
    if (selectedBoat && resultsRef.current) {
      setTimeout(() => {
        resultsRef.current?.scrollIntoView({
          behavior: "smooth",
          block: "start",
        });
      }, 100);
    }
  }, [selectedBoat]);

  return (
    <>
      {/* Search section — overlapping hero, pulled well up into it */}
      <section className="relative z-30 -mt-40 sm:-mt-48 px-6 pb-16">
        <div className="max-w-2xl mx-auto text-center mb-4">
          <p className="body-text text-white/70 text-sm sm:text-base font-light italic" style={{ textShadow: "0 1px 8px rgba(0,0,0,0.3)" }}>
            Find your boat and we&rsquo;ll show you what we found.
          </p>
        </div>
        <SearchBar onBoatSelected={handleBoatSelected} />
      </section>

      {/* Results section */}
      {selectedBoat && (
        <section ref={resultsRef} className="px-6 pb-24 space-y-10">
          {/* Boat identity card */}
          <BoatCard
            key={selectedBoat.id}
            boatId={selectedBoat.id}
            boatName={selectedBoat.boat_name}
            onBoatLoaded={handleBoatLoaded}
          />

          {/* Teaser analysis — auto-streams when boat is loaded */}
          {boatLoaded && boatDetail && (
            <TeaserAnalysis
              key={`teaser-${selectedBoat.id}`}
              boatId={selectedBoat.id}
              boatName={selectedBoat.boat_name}
            />
          )}

          {/* Purchase CTA — appears after boat is loaded */}
          {boatLoaded && boatDetail && (
            <PurchaseCTA
              boatId={selectedBoat.id}
              boatName={selectedBoat.boat_name}
            />
          )}
        </section>
      )}
    </>
  );
}

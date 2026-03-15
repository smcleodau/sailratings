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
      {/* Search section — overlapping hero bottom */}
      <section className="relative z-30 -mt-24 px-6 pb-16">
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

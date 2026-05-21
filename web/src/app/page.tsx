"use client";

import { useState, useEffect } from "react";
import { Loader2 } from "lucide-react";
import Hero from "@/components/Hero";
import ReportModal from "@/components/ReportModal";
import { getBoat, type SearchResult, type BoatDetail } from "@/lib/api";

export default function Home() {
  const [selectedBoat, setSelectedBoat] = useState<SearchResult | null>(null);
  const [boatDetail, setBoatDetail] = useState<BoatDetail | null>(null);
  const [boatError, setBoatError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");

  const handleBoatSelected = (boat: SearchResult) => {
    setSelectedBoat(boat);
    setBoatDetail(null);
    setBoatError(null);
    setSearchQuery(boat.boat_name);
  };

  const handleClose = () => {
    setSelectedBoat(null);
    setBoatDetail(null);
    setBoatError(null);
  };

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

  return (
    <main className="min-h-screen bg-cream">
      <Hero onBoatSelected={handleBoatSelected} />

      {/* While the boat detail is loading, show a minimal scrim overlay
          so the user knows the dossier is being prepared. */}
      {selectedBoat && !boatDetail && !boatError && (
        <div
          className="fixed inset-0 z-[100] flex items-center justify-center bg-navy/85 backdrop-blur-sm dossier-backdrop"
          role="status"
          aria-live="polite"
        >
          <div className="flex flex-col items-center gap-4 text-cream">
            <Loader2 size={20} className="animate-spin text-brass" />
            <span className="data-mono text-[11px] uppercase tracking-[0.22em] text-cream/80">
              Pulling {selectedBoat.boat_name}&rsquo;s file
            </span>
          </div>
        </div>
      )}

      {selectedBoat && boatError && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center bg-navy/85 backdrop-blur-sm">
          <div className="bg-cream max-w-md px-8 py-6 border-t-2 border-brass">
            <p className="body-text text-charcoal text-[14px]">{boatError}</p>
            <button
              onClick={handleClose}
              className="data-mono text-[11px] uppercase tracking-[0.18em] text-brass mt-4 hover:text-brass-dark"
            >
              Close
            </button>
          </div>
        </div>
      )}

      {selectedBoat && boatDetail && (
        <ReportModal
          key={`modal-${selectedBoat.id}`}
          boat={boatDetail}
          searchQuery={searchQuery}
          onClose={handleClose}
        />
      )}

      {!selectedBoat && (
        <footer className="border-t border-border px-6 py-8 bg-cream">
          <div className="max-w-2xl mx-auto flex flex-col sm:flex-row items-center justify-between gap-3 text-[11px] text-muted">
            <span className="brand-wordmark text-muted/40">Sail Ratings</span>
            <span className="body-text text-center">
              Rating data sourced from public certificates. Not affiliated with the RORC Rating Office or ORC.
            </span>
          </div>
        </footer>
      )}
    </main>
  );
}

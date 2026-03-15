"use client";

import { useState, useEffect } from "react";
import {
  Anchor,
  MapPin,
  Ruler,
  Calendar,
  Users,
  Move,
} from "lucide-react";
import { getBoat, type BoatDetail } from "@/lib/api";

interface BoatCardProps {
  boatId: number;
  boatName: string;
  onBoatLoaded: (boat: BoatDetail) => void;
}

export default function BoatCard({
  boatId,
  boatName,
  onBoatLoaded,
}: BoatCardProps) {
  const [boat, setBoat] = useState<BoatDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      setLoading(true);
      setError(null);
      try {
        const data = await getBoat(boatId);
        if (!cancelled) {
          setBoat(data);
          onBoatLoaded(data);
        }
      } catch {
        if (!cancelled) {
          setError("Failed to load boat details.");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    load();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [boatId]);

  if (loading) {
    return (
      <div className="w-full max-w-2xl mx-auto border border-border bg-white p-8">
        <div className="flex items-center gap-3">
          <div
            className="w-4 h-4 border border-border border-t-navy animate-spin"
            style={{ borderRadius: "50%" }}
          />
          <span className="body-text text-muted">
            Loading {boatName}...
          </span>
        </div>
      </div>
    );
  }

  if (error || !boat) {
    return (
      <div className="w-full max-w-2xl mx-auto border border-border bg-white p-8">
        <p className="body-text text-copper">{error || "Boat not found."}</p>
      </div>
    );
  }

  const tcc = boat.irc_tcc;

  return (
    <div className="w-full max-w-2xl mx-auto border border-border bg-white">
      {/* Header bar */}
      <div className="border-b border-border-light px-8 py-6">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="heading-serif text-3xl sm:text-4xl text-ink">
              {boat.boat_name}
            </h2>
            <div className="flex items-center gap-3 mt-2">
              <span className="data-mono text-sm text-muted">
                {boat.sail_number}
              </span>
              {boat.design && (
                <>
                  <span className="text-border">|</span>
                  <span className="body-text text-sm text-muted">
                    {boat.design}
                  </span>
                </>
              )}
            </div>
          </div>
          {tcc != null && (
            <div className="text-right flex-shrink-0">
              <div className="data-mono text-3xl sm:text-4xl text-teal font-semibold">
                {Number(tcc).toFixed(3)}
              </div>
              <div className="text-xs text-muted uppercase tracking-wider mt-1">
                TCC
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Detail grid */}
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-px bg-border-light">
        {boat.country && (
          <DetailCell
            icon={<MapPin size={16} strokeWidth={1.5} />}
            label="Country"
            value={boat.country}
          />
        )}
        {boat.design && (
          <DetailCell
            icon={<Anchor size={16} strokeWidth={1.5} />}
            label="Design"
            value={boat.design}
          />
        )}
        {boat.year_built != null && (
          <DetailCell
            icon={<Calendar size={16} strokeWidth={1.5} />}
            label="Year Built"
            value={String(boat.year_built)}
          />
        )}
        {boat.irc_non_spi_tcc != null && (
          <DetailCell
            icon={<Ruler size={16} strokeWidth={1.5} />}
            label="Non-Spi TCC"
            value={Number(boat.irc_non_spi_tcc).toFixed(3)}
            mono
          />
        )}
        {boat.irc_crew != null && (
          <DetailCell
            icon={<Users size={16} strokeWidth={1.5} />}
            label="Crew"
            value={String(boat.irc_crew)}
            mono
          />
        )}
        {boat.irc_lh != null && boat.irc_beam != null && boat.irc_draft != null && (
          <DetailCell
            icon={<Move size={16} strokeWidth={1.5} />}
            label="LH / Beam / Draft"
            value={`${boat.irc_lh}m / ${boat.irc_beam}m / ${boat.irc_draft}m`}
            mono
          />
        )}
      </div>
    </div>
  );
}

function DetailCell({
  icon,
  label,
  value,
  mono = false,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="bg-white px-6 py-4">
      <div className="flex items-center gap-2 text-muted mb-1">
        {icon}
        <span className="text-xs uppercase tracking-wider">{label}</span>
      </div>
      <div
        className={`text-ink ${
          mono ? "data-mono text-sm" : "body-text text-base"
        }`}
      >
        {value}
      </div>
    </div>
  );
}

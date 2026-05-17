"use client";

import { useMemo, useState } from "react";
import { Check, Loader2, Pencil, X } from "lucide-react";
import { submitCorrection, type BoatDetail, type CorrectionField } from "@/lib/api";

interface PinnedMastheadProps {
  boat: BoatDetail;
  /** Optional subline shown under the masthead row (e.g. "File compiled. Opening…"). */
  subline?: React.ReactNode;
}

function nowStamp(): string {
  const d = new Date();
  const day = String(d.getUTCDate()).padStart(2, "0");
  const mon = d.toLocaleString("en-GB", { month: "short", timeZone: "UTC" });
  const yr = d.getUTCFullYear();
  const hh = String(d.getUTCHours()).padStart(2, "0");
  const mm = String(d.getUTCMinutes()).padStart(2, "0");
  return `${day} ${mon} ${yr} · ${hh}:${mm} UTC`;
}

function EditableField({
  boatId,
  label,
  value,
  fieldName,
}: {
  boatId: number;
  label: string;
  value: string | number | null | undefined;
  fieldName: CorrectionField;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value != null ? String(value) : "");
  const [pending, setPending] = useState(false);
  const [submitted, setSubmitted] = useState(false);

  const send = async () => {
    const v = draft.trim();
    if (!v) return;
    setPending(true);
    try {
      await submitCorrection(boatId, { field_name: fieldName, proposed_value: v });
      setSubmitted(true);
      setEditing(false);
    } catch {
      // Moderation queue is best-effort.
    } finally {
      setPending(false);
    }
  };

  if (editing) {
    return (
      <span className="inline-flex items-center gap-1.5 align-baseline">
        <input
          autoFocus
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") void send();
            if (e.key === "Escape") setEditing(false);
          }}
          className="bg-cream border border-brass/50 text-charcoal px-2 py-0.5 text-[13px] font-body w-40 focus:outline-none focus:border-brass"
        />
        <button
          onClick={() => void send()}
          disabled={pending || !draft.trim()}
          className="text-brass hover:text-brass-dark disabled:opacity-30"
          aria-label="Submit correction"
        >
          {pending ? <Loader2 size={12} className="animate-spin" /> : <Check size={12} />}
        </button>
        <button
          onClick={() => setEditing(false)}
          className="text-charcoal/30 hover:text-charcoal"
          aria-label="Cancel"
        >
          <X size={12} />
        </button>
      </span>
    );
  }

  return (
    <span className="inline-flex items-baseline gap-1.5 group">
      <span className="data-mono text-[10px] uppercase tracking-[0.16em] text-charcoal/55 mr-0.5 font-semibold">
        {label}
      </span>
      <span className="body-text text-charcoal text-[13px]">
        {value != null && String(value).trim() !== "" ? value : (
          <span className="text-charcoal/40 italic">unknown</span>
        )}
      </span>
      {submitted ? (
        <span className="data-mono text-[9px] uppercase tracking-wider text-brass ml-1">
          pending review
        </span>
      ) : (
        <button
          onClick={() => setEditing(true)}
          className="text-brass/50 hover:text-brass transition-colors ml-1 inline-flex items-center"
          aria-label={`Correct ${label.toLowerCase()}`}
          title={`Correct ${label.toLowerCase()}`}
        >
          <Pencil size={11} strokeWidth={1.75} />
        </button>
      )}
    </span>
  );
}

export default function PinnedMasthead({ boat, subline }: PinnedMastheadProps) {
  const dateline = useMemo(() => nowStamp(), []);

  const tcc = boat.irc_tcc;
  const ratingLabel = tcc != null ? "IRC" : boat.orc_gph != null ? "ORC" : null;
  const ratingValue = tcc != null
    ? Number(tcc).toFixed(4)
    : boat.orc_gph != null
    ? Number(boat.orc_gph).toFixed(1)
    : null;
  const ratingUnit = tcc != null ? "TCC" : "GPH";

  return (
    <div className="sticky top-0 z-30">
      {/* Navy strip — wordmark + dateline */}
      <div className="bg-navy text-cream px-6 sm:px-12 h-[60px] sm:h-[72px] flex items-center justify-between border-b border-navy-light">
        <span className="brand-wordmark text-cream text-[15px] sm:text-base">
          Sail Ratings
        </span>
        <span className="data-mono text-[10px] uppercase tracking-[0.18em] text-cream/70 hidden sm:inline">
          The Bench · {dateline}
        </span>
        <span className="data-mono text-[10px] uppercase tracking-[0.18em] text-cream/70 sm:hidden">
          The Bench
        </span>
      </div>

      {/* Cream masthead row — boat name, TCC, metadata */}
      <div className="bg-cream border-b border-charcoal/15 px-6 sm:px-12 py-3.5 sm:py-4">
        <div className="flex items-baseline justify-between gap-4 flex-wrap">
          <div className="flex items-baseline gap-4 flex-wrap min-w-0">
            <h1
              className="heading-display text-charcoal uppercase tracking-[-0.015em] leading-none truncate"
              style={{ fontSize: "clamp(1.3rem, 2.4vw, 1.9rem)" }}
            >
              {boat.boat_name}
            </h1>
            {boat.sail_number && (
              <span className="data-mono text-[13px] text-charcoal/85 font-semibold">
                {boat.sail_number}
              </span>
            )}
            {ratingValue && (
              <span className="data-mono text-charcoal font-semibold text-[15px] sm:text-[17px]">
                <span className="text-brass mr-1.5">{ratingLabel}</span>
                {ratingValue}
                <span className="text-charcoal/60 ml-1 text-[11px] uppercase tracking-[0.12em] font-semibold">
                  {ratingUnit}
                </span>
              </span>
            )}
          </div>
          <div className="flex flex-wrap items-baseline gap-x-5 gap-y-1.5 text-[12px]">
            {boat.design && (
              <EditableField
                boatId={boat.id}
                label="Design"
                value={boat.design}
                fieldName="design_canonical"
              />
            )}
            <EditableField
              boatId={boat.id}
              label="Year"
              value={boat.year_built}
              fieldName="year_built"
            />
            <EditableField
              boatId={boat.id}
              label="Designer"
              value={boat.designer}
              fieldName="designer"
            />
            <EditableField
              boatId={boat.id}
              label="Builder"
              value={boat.builder}
              fieldName="builder"
            />
          </div>
        </div>
        {subline && (
          <div className="mt-2 data-mono text-[10px] uppercase tracking-[0.16em] text-charcoal/65 font-semibold">
            {subline}
          </div>
        )}
      </div>
    </div>
  );
}

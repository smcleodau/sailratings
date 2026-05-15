"use client";

import { useEffect, useMemo, useState } from "react";
import { Check, Edit3 } from "lucide-react";
import { submitCorrection, type BoatDetail, type CorrectionField } from "@/lib/api";

type FieldKey = "designer" | "builder" | "year_built" | "design_canonical";

const FIELD_LABELS: Record<FieldKey, string> = {
  designer: "Designer",
  builder: "Builder",
  year_built: "Year built",
  design_canonical: "Class",
};

function currentValue(boat: BoatDetail, key: FieldKey): string | null {
  const v = boat[key];
  if (v == null) return null;
  return String(v);
}

export default function CorrectionForm({ boat }: { boat: BoatDetail }) {
  const [edits, setEdits] = useState<Record<FieldKey, string>>({
    designer: currentValue(boat, "designer") ?? "",
    builder: currentValue(boat, "builder") ?? "",
    year_built: currentValue(boat, "year_built") ?? "",
    design_canonical: currentValue(boat, "design_canonical") ?? "",
  });
  const [email, setEmail] = useState("");
  const [editing, setEditing] = useState<Set<FieldKey>>(new Set());
  const [submitted, setSubmitted] = useState<Set<FieldKey>>(new Set());
  const [error, setError] = useState<string | null>(null);

  const fields = useMemo(() => Object.keys(FIELD_LABELS) as FieldKey[], []);

  useEffect(() => {
    const t = track("correction_form_shown", {
      boat_id: boat.id,
      missing_count: fields.filter((f) => currentValue(boat, f) == null).length,
    });
    void t;
  }, [boat.id, fields, boat]);

  const send = async (field: FieldKey) => {
    setError(null);
    const value = edits[field].trim();
    if (!value) {
      setError("Please enter a value before submitting");
      return;
    }
    try {
      await submitCorrection(boat.id, {
        field_name: field as CorrectionField,
        proposed_value: value,
        submitted_email: email.trim() || undefined,
      });
      setSubmitted((prev) => new Set(prev).add(field));
      setEditing((prev) => {
        const next = new Set(prev);
        next.delete(field);
        return next;
      });
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Failed to submit";
      setError(msg);
    }
  };

  const toggleEdit = (field: FieldKey) => {
    setEditing((prev) => {
      const next = new Set(prev);
      if (next.has(field)) next.delete(field);
      else next.add(field);
      return next;
    });
  };

  return (
    <div className="w-full max-w-2xl mx-auto border border-border bg-white">
      <div className="border-b border-border-light px-6 py-4">
        <h3 className="heading-display text-xl text-charcoal">
          Help us improve this profile
        </h3>
        <p className="body-text text-sm text-muted mt-1">
          Confirm what&apos;s right or correct what&apos;s wrong. Submissions go
          to Stuart for review.
        </p>
      </div>

      <div className="divide-y divide-border-light">
        {fields.map((field) => {
          const current = currentValue(boat, field);
          const isEditing = editing.has(field);
          const isSubmitted = submitted.has(field);
          const hasGuess = current != null && current !== "";

          return (
            <div key={field} className="px-6 py-4">
              <div className="flex items-center gap-3">
                <div className="flex-1 min-w-0">
                  <p className="text-xs uppercase tracking-wider text-muted mb-1">
                    {FIELD_LABELS[field]}
                  </p>
                  {isEditing ? (
                    <input
                      type={field === "year_built" ? "number" : "text"}
                      inputMode={field === "year_built" ? "numeric" : "text"}
                      value={edits[field]}
                      onChange={(e) =>
                        setEdits({ ...edits, [field]: e.target.value })
                      }
                      placeholder={
                        hasGuess
                          ? "Correct value..."
                          : `Add ${FIELD_LABELS[field].toLowerCase()}...`
                      }
                      className="w-full border border-border px-3 py-1.5 text-sm body-text focus:border-brass focus:outline-none"
                      autoFocus
                    />
                  ) : (
                    <p className="body-text text-sm text-charcoal">
                      {hasGuess ? (
                        current
                      ) : (
                        <span className="text-muted italic">unknown</span>
                      )}
                    </p>
                  )}
                </div>

                <div className="flex items-center gap-2 flex-shrink-0">
                  {!isSubmitted && hasGuess && !isEditing && (
                    <button
                      onClick={() => send(field)}
                      className="text-xs body-text text-navy hover:text-brass border border-border px-3 py-1.5 transition-colors"
                      title="Confirm this is correct"
                    >
                      <Check size={12} className="inline mr-1" />
                      Confirm
                    </button>
                  )}
                  {!isSubmitted && (
                    <button
                      onClick={() =>
                        isEditing ? send(field) : toggleEdit(field)
                      }
                      className="text-xs body-text text-charcoal hover:text-brass border border-border px-3 py-1.5 transition-colors"
                    >
                      {isEditing ? (
                        "Submit"
                      ) : (
                        <>
                          <Edit3 size={12} className="inline mr-1" />
                          {hasGuess ? "Correct" : "Add"}
                        </>
                      )}
                    </button>
                  )}
                  {isSubmitted && (
                    <span className="text-xs body-text text-signal-light flex items-center gap-1">
                      <Check size={14} strokeWidth={2} />
                      Thanks!
                    </span>
                  )}
                </div>
              </div>
            </div>
          );
        })}
      </div>

      <div className="px-6 py-4 border-t border-border-light bg-cream/30">
        <label className="block text-xs uppercase tracking-wider text-muted mb-1">
          Email (optional, to credit you)
        </label>
        <input
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="you@example.com"
          className="w-full border border-border px-3 py-1.5 text-sm body-text focus:border-brass focus:outline-none"
        />
      </div>

      {error && (
        <div className="px-6 py-3 border-t border-border-light">
          <p className="text-xs text-brass body-text">{error}</p>
        </div>
      )}
    </div>
  );
}

async function track(event: string, properties?: Record<string, unknown>) {
  try {
    const { track: posthogTrack } = await import("@/lib/posthog");
    posthogTrack(event, properties);
  } catch {
    /* PostHog optional */
  }
}

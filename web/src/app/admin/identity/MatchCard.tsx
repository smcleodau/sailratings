"use client";

import { useEffect, useState } from "react";
import {
  ArrowLeftRightIcon,
  FlagIcon,
  GitMergeIcon,
  PauseIcon,
  RotateCcwIcon,
  ShieldAlertIcon,
  UserIcon,
  XIcon,
} from "@/components/admin/AdminIcons";

/**
 * MatchCard — the AD-01-04 evidence view, certified against the DP-04-05
 * adjudication contracts.
 *
 * Renders one adjudication case (QueueItemV1): side-by-side source
 * evidence, the score explanation, downstream impact, and the reversible
 * actions.  High-impact merges surface the double-review state and the
 * reviewer chain.
 */

export interface MatchCardData {
  case_id: string;
  status: string;
  queue_reason: string;
  priority: number;
  pair: {
    left_id: string;
    right_id: string;
    rules_fired: string[];
    matching_keys: string[];
    ruleset_id: string;
  };
  score: number;
  score_explanation: string[];
  impact: string;
  impact_flags: string[];
  left_evidence: Record<string, unknown>;
  right_evidence: Record<string, unknown>;
  actions: string[];
  requires_second_review: boolean;
  votes: Array<{ decision: string; decided_by: string; decided_at: string }>;
  enqueued_at: string;
}

export type AdjudicationAction = "merge" | "separate" | "escalate" | "defer";

const EVIDENCE_FIELDS: Array<{ key: string; label: string }> = [
  { key: "name", label: "Name" },
  { key: "sail_number", label: "Sail №" },
  { key: "registry_id", label: "Registry ID" },
  { key: "design", label: "Design" },
  { key: "country", label: "Country" },
  { key: "year_built", label: "Built" },
  { key: "loa_m", label: "LOA (m)" },
  { key: "valid_from", label: "Valid from" },
  { key: "valid_to", label: "Valid to" },
  { key: "source", label: "Source" },
];

function fmt(v: unknown): string {
  if (v === null || v === undefined || v === "") return "—";
  return String(v);
}

function valuesDiffer(a: unknown, b: unknown): boolean {
  const sa = fmt(a);
  const sb = fmt(b);
  if (sa === "—" || sb === "—") return false;
  return sa.toUpperCase().replace(/\s+/g, " ") !== sb.toUpperCase().replace(/\s+/g, " ");
}

function scoreColor(score: number): string {
  if (score >= 0.8) return "text-[var(--sr-status-success)]";
  if (score >= 0.4) return "text-[var(--sr-status-warning)]";
  return "text-[var(--sr-status-danger)]";
}

function impactBadge(impact: string) {
  const styles: Record<string, string> = {
    high: "bg-[var(--sr-status-danger)]/15 text-[var(--sr-status-danger)] border-[var(--sr-status-danger)]/40",
    medium: "bg-[var(--sr-status-warning)]/15 text-[var(--sr-status-warning)] border-[var(--sr-status-warning)]/40",
    low: "bg-[var(--sr-status-success)]/15 text-[var(--sr-status-success)] border-[var(--sr-status-success)]/40",
  };
  return (
    <span
      className={`text-[10px] uppercase tracking-[0.14em] px-2 py-[2px] rounded-full border ${styles[impact] ?? styles.low}`}
    >
      {impact} impact
    </span>
  );
}

export function MatchCard({
  item,
  acting,
  isTop,
  onDecide,
  onReverse,
  lastResolutionId,
}: {
  item: MatchCardData;
  acting: boolean;
  isTop?: boolean;
  onDecide: (caseId: string, action: AdjudicationAction) => void;
  onReverse?: (resolutionId: string) => void;
  lastResolutionId?: string | null;
}) {
  const [confirming, setConfirming] = useState(false);
  const awaitingSecond =
    item.status === "awaiting_second_review" && item.requires_second_review;
  const mergeVotes = item.votes.filter((v) => v.decision === "merge").length;

  const handleMerge = () => {
    // High-impact merges are consequential: confirm intent in the UI
    // (the backend independently enforces the two-reviewer rule).
    if (item.requires_second_review && !awaitingSecond && !confirming) {
      setConfirming(true);
      return;
    }
    setConfirming(false);
    onDecide(item.case_id, "merge");
  };

  useEffect(() => {
    if (!isTop || acting) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      // Ignore if user is typing in an input field
      if (document.activeElement?.tagName === "INPUT" || document.activeElement?.tagName === "TEXTAREA") return;
      
      switch (e.key) {
        case "m":
        case "M":
          handleMerge();
          break;
        case "s":
        case "S":
          onDecide(item.case_id, "separate");
          break;
        case "e":
        case "E":
          onDecide(item.case_id, "escalate");
          break;
        case "d":
        case "D":
          onDecide(item.case_id, "defer");
          break;
        case "z":
        case "Z":
        case "u":
        case "U":
          if (onReverse && lastResolutionId) {
            onReverse(lastResolutionId);
          }
          break;
        case "Escape":
          if (confirming) setConfirming(false);
          break;
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isTop, acting, handleMerge, onDecide, item.case_id, onReverse, lastResolutionId, confirming]);

  return (
    <article
      className="rounded-xl border border-[var(--sr-marine-600)]/40 bg-[var(--sr-ink-800)]/60 backdrop-blur p-5 space-y-4"
      data-testid="match-card"
      data-case-id={item.case_id}
    >
      {/* Header: provenance + score + impact */}
      <header className="flex flex-wrap items-center gap-2 justify-between">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-[10px] uppercase tracking-[0.14em] text-[var(--sr-marine-200)] border border-[var(--sr-marine-600)]/40 rounded-full px-2 py-[2px]">
            {item.queue_reason.replace(/_/g, " ")}
          </span>
          {impactBadge(item.impact)}
          {item.impact_flags.map((f) => (
            <span
              key={f}
              className="text-[10px] uppercase tracking-[0.12em] text-[var(--sr-text-secondary)] border border-[var(--sr-marine-600)]/30 rounded-full px-2 py-[2px]"
            >
              {f.replace(/_/g, " ")}
            </span>
          ))}
          {item.requires_second_review && (
            <span className="flex items-center gap-1 text-[10px] uppercase tracking-[0.12em] text-[var(--sr-status-danger)]">
              <ShieldAlertIcon size={12} /> double review
            </span>
          )}
        </div>
        <div className="text-right">
          <div className={`text-2xl font-semibold tabular-nums ${scoreColor(item.score)}`}>
            {(item.score * 100).toFixed(0)}%
          </div>
          <div className="text-[10px] uppercase tracking-[0.14em] text-[var(--sr-text-secondary)]">
            match score
          </div>
        </div>
      </header>

      {/* Side-by-side source evidence */}
      <div className="grid grid-cols-[1fr_auto_1fr] items-stretch gap-3">
        <EvidencePane title={item.pair.left_id} evidence={item.left_evidence} other={item.right_evidence} />
        <div className="flex items-center text-[var(--sr-marine-200)]">
          <ArrowLeftRightIcon size={16} />
        </div>
        <EvidencePane title={item.pair.right_id} evidence={item.right_evidence} other={item.left_evidence} />
      </div>

      {/* Score explanation + rule provenance */}
      <section className="rounded-lg border border-[var(--sr-marine-600)]/30 p-3 space-y-2">
        <h3 className="text-[10px] uppercase tracking-[0.16em] text-[var(--sr-text-secondary)]">
          Score explanation
        </h3>
        <ul className="space-y-1">
          {item.score_explanation.map((line, i) => (
            <li key={i} className="text-sm text-[var(--sr-text-primary)]/90 font-mono">
              {line}
            </li>
          ))}
        </ul>
        <div className="flex flex-wrap gap-1 pt-1">
          {item.pair.rules_fired.map((r) => (
            <span
              key={r}
              title={item.pair.ruleset_id}
              className="text-[10px] font-mono px-2 py-[2px] rounded bg-[var(--sr-marine-600)]/20 text-[var(--sr-marine-200)]"
            >
              {r}
            </span>
          ))}
          {item.pair.matching_keys.map((k) => (
            <span
              key={k}
              className="text-[10px] font-mono px-2 py-[2px] rounded bg-[var(--sr-marine-600)]/10 text-[var(--sr-text-secondary)]"
            >
              {k}
            </span>
          ))}
        </div>
      </section>

      {/* Double-review state */}
      {item.requires_second_review && (
        <section className="rounded-lg border border-[var(--sr-status-danger)]/30 bg-[var(--sr-status-danger)]/5 p-3">
          <div className="text-[10px] uppercase tracking-[0.16em] text-[var(--sr-status-danger)] mb-1">
            Review chain ({mergeVotes}/2 merge votes)
          </div>
          <div className="flex flex-wrap gap-2">
            {item.votes.map((v, i) => (
              <span
                key={i}
                className="flex items-center gap-1 text-xs text-[var(--sr-text-primary)]/80"
              >
                <UserIcon size={12} /> {v.decided_by}: {v.decision}
              </span>
            ))}
            {item.votes.length === 0 && (
              <span className="text-xs text-[var(--sr-text-secondary)]">
                No reviews yet — two distinct reviewers required to merge.
              </span>
            )}
            {awaitingSecond && (
              <span className="text-xs text-[var(--sr-status-warning)]">
                Awaiting a second, distinct reviewer.
              </span>
            )}
          </div>
        </section>
      )}

      {/* Actions — every action is reversible */}
      <footer className="flex flex-wrap items-center gap-2 pt-1">
        <button
          type="button"
          disabled={acting}
          onClick={handleMerge}
          className={`flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-medium transition-colors disabled:opacity-40 ${
            confirming
              ? "bg-[var(--sr-status-danger)] text-[var(--sr-text-primary)]"
              : "bg-[var(--sr-status-success)]/80 hover:bg-[var(--sr-status-success)] text-[var(--sr-text-primary)]"
          }`}
        >
          <GitMergeIcon size={15} />
          {confirming
            ? "Confirm merge (starts double review)"
            : awaitingSecond
              ? "Merge (second review)"
              : "Merge"}
          {isTop && (
            <kbd className="ml-1 font-mono text-[10px] opacity-60">M</kbd>
          )}
        </button>
        {confirming && (
          <button
            type="button"
            onClick={() => setConfirming(false)}
            className="text-xs text-[var(--sr-text-secondary)] underline"
          >
            cancel
          </button>
        )}
        <button
          type="button"
          disabled={acting}
          onClick={() => onDecide(item.case_id, "separate")}
          className="flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-medium bg-[var(--sr-marine-600)]/40 hover:bg-[var(--sr-marine-600)]/70 text-[var(--sr-text-primary)] transition-colors disabled:opacity-40"
        >
          <XIcon size={15} /> Keep separate
          {isTop && (
            <kbd className="ml-1 font-mono text-[10px] opacity-60">S</kbd>
          )}
        </button>
        <button
          type="button"
          disabled={acting}
          onClick={() => onDecide(item.case_id, "escalate")}
          className="flex items-center gap-2 rounded-lg px-3 py-2 text-xs bg-[var(--sr-status-warning)]/10 hover:bg-[var(--sr-status-warning)]/20 text-[var(--sr-status-warning)] border border-[var(--sr-status-warning)]/30 transition-colors disabled:opacity-40"
        >
          <FlagIcon size={13} /> Escalate
          {isTop && (
            <kbd className="ml-1 font-mono text-[10px] opacity-60">E</kbd>
          )}
        </button>
        <button
          type="button"
          disabled={acting}
          onClick={() => onDecide(item.case_id, "defer")}
          className="flex items-center gap-2 rounded-lg px-3 py-2 text-xs bg-[var(--sr-marine-600)]/10 hover:bg-[var(--sr-marine-600)]/30 text-[var(--sr-text-secondary)] transition-colors disabled:opacity-40"
        >
          <PauseIcon size={13} /> Defer
          {isTop && (
            <kbd className="ml-1 font-mono text-[10px] opacity-60">D</kbd>
          )}
        </button>
        {onReverse && lastResolutionId && (
          <button
            type="button"
            disabled={acting}
            onClick={() => onReverse(lastResolutionId)}
            className="ml-auto flex items-center gap-2 rounded-lg px-3 py-2 text-xs text-[var(--sr-status-danger)] border border-[var(--sr-status-danger)]/30 hover:bg-[var(--sr-status-danger)]/10 transition-colors disabled:opacity-40"
          >
            <RotateCcwIcon size={13} /> Undo last decision
            {isTop && (
              <kbd className="ml-1 font-mono text-[10px] opacity-60">U</kbd>
            )}
          </button>
        )}
      </footer>
    </article>
  );
}

function EvidencePane({
  title,
  evidence,
  other,
}: {
  title: string;
  evidence: Record<string, unknown>;
  other: Record<string, unknown>;
}) {
  return (
    <div className="rounded-lg border border-[var(--sr-marine-600)]/30 overflow-hidden">
      <div className="px-3 py-2 bg-[var(--sr-marine-600)]/20 text-[11px] font-mono text-[var(--sr-marine-200)] truncate">
        {title}
      </div>
      <dl className="divide-y divide-[var(--sr-marine-600)]/20">
        {EVIDENCE_FIELDS.map(({ key, label }) => {
          const value = evidence[key];
          const differs = valuesDiffer(value, other[key]);
          return (
            <div key={key} className="grid grid-cols-[86px_1fr] px-3 py-1.5">
              <dt className="text-[10px] uppercase tracking-[0.1em] text-[var(--sr-text-secondary)] self-center">
                {label}
              </dt>
              <dd
                className={`text-sm truncate ${differs ? "text-[var(--sr-status-warning)] font-medium" : "text-[var(--sr-text-primary)]/90"}`}
                title={fmt(value)}
              >
                {key === "source" ? (
                  <span className="text-[10px] uppercase tracking-[0.1em] text-[var(--sr-marine-200)]">
                    {fmt(value)}
                  </span>
                ) : (
                  fmt(value)
                )}
              </dd>
            </div>
          );
        })}
      </dl>
    </div>
  );
}

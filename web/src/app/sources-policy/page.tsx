"use client";

import { useEffect, useState, useCallback } from "react";
import {
  CheckCircle2,
  AlertTriangle,
  Shield,
  Clock,
  FileText,
  Globe,
  Lock,
  HelpCircle,
  Ban,
  RefreshCw,
} from "lucide-react";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "/api/v1";

/* ── Types ────────────────────────────────────────────────────────────── */

interface PolicySource {
  slug: string;
  display_name: string;
  base_url: string;
  category: string;
  policy_version: string;
  legal_status: string;
  source_class: string;
  content_type: string;
  classification: string;
  enabled: boolean;
  quarantined: boolean;
  robots_disallow: string[];
  notes: string | null;
}

interface PolicySummary {
  version: string;
  approved_date: string;
  authority: string;
  authority_email: string;
  user_agent: string;
  issue_label: string;
  spec_reference: string;
  counts: {
    approved: number;
    hold: number;
    blocked: number;
    total: number;
  };
  sources: PolicySource[];
}

/* ── Static fallback data (matches policy.py) ─────────────────────────── */

const STATIC_POLICY: PolicySummary = {
  version: "interim-v0",
  approved_date: "2026-08-30",
  authority: "Stuart McLeod",
  authority_email: "stuart@sailratings.com",
  user_agent: "SailRatings/1.0 (+https://sailratings.com; contact=stuart@sailratings.com)",
  issue_label: "DP-01-02",
  spec_reference: "SPEC-012",
  counts: { approved: 9, hold: 2, blocked: 0, total: 11 },
  sources: [
    { slug: "sailsys", display_name: "SailSys", base_url: "https://www.sailsys.com.au", category: "results", policy_version: "interim-v0", legal_status: "approved", source_class: "public", content_type: "html", classification: "approved", enabled: true, quarantined: false, robots_disallow: [], notes: "Australian race management; publicly published results" },
    { slug: "topyacht", display_name: "TopYacht", base_url: "https://www.topyacht.com.au", category: "results", policy_version: "interim-v0", legal_status: "approved", source_class: "public", content_type: "html", classification: "approved", enabled: true, quarantined: false, robots_disallow: [], notes: "Australian race management; publicly published results" },
    { slug: "irc-tcc", display_name: "IRC TCC Listings", base_url: "https://ircrating.org", category: "ratings", policy_version: "interim-v0", legal_status: "approved", source_class: "public", content_type: "file", classification: "approved", enabled: true, quarantined: false, robots_disallow: [], notes: "Published for racing administration; CSV download from ircrating.org" },
    { slug: "orc", display_name: "ORC", base_url: "https://data.orc.org", category: "ratings", policy_version: "interim-v0", legal_status: "approved", source_class: "public", content_type: "api", classification: "approved", enabled: true, quarantined: false, robots_disallow: [], notes: "Published for racing administration; JSON API from data.orc.org" },
    { slug: "yachtscoring", display_name: "Yacht Scoring", base_url: "https://www.yachtscoring.com", category: "results", policy_version: "interim-v0", legal_status: "approved", source_class: "public", content_type: "html", classification: "approved", enabled: true, quarantined: false, robots_disallow: [], notes: "US/international race results; publicly published" },
    { slug: "manage2sail", display_name: "Manage2Sail", base_url: "https://manage2sail.com", category: "results", policy_version: "interim-v0", legal_status: "approved", source_class: "public", content_type: "html", classification: "approved", enabled: true, quarantined: false, robots_disallow: [], notes: "European race management; publicly published results" },
    { slug: "sailwave", display_name: "Sailwave", base_url: "https://www.sailwave.com", category: "results", policy_version: "interim-v0", legal_status: "approved", source_class: "public", content_type: "file", classification: "approved", enabled: true, quarantined: false, robots_disallow: [], notes: "Results files publicly linked from club sites" },
    { slug: "sailing-news", display_name: "Sailing News Feeds", base_url: "https://www.sailingnews.com", category: "news", policy_version: "interim-v0", legal_status: "approved", source_class: "public", content_type: "feed", classification: "approved", enabled: true, quarantined: false, robots_disallow: [], notes: "RSS/Atom feeds; explicitly published for syndication" },
    { slug: "irc-certs", display_name: "IRC Certificate PDFs", base_url: "https://ircrating.org/pdfdirectory", category: "certificates", policy_version: "interim-v0", legal_status: "approved", source_class: "public", content_type: "pdf", classification: "approved", enabled: true, quarantined: false, robots_disallow: [], notes: "Published for racing administration; publicly accessible; core platform data. Attribution: X-SailRatings-Source: irc-certs" },
    { slug: "clubspot", display_name: "ClubSpot", base_url: "https://clubspot.com", category: "results", policy_version: "interim-v0", legal_status: "hold", source_class: "unclear", content_type: "html", classification: "hold", enabled: true, quarantined: false, robots_disallow: [], notes: "Rights ruling pending; ToS review incomplete" },
    { slug: "kwindoo", display_name: "Kwindoo", base_url: "https://www.kwindoo.com", category: "results", policy_version: "interim-v0", legal_status: "hold", source_class: "unclear", content_type: "html", classification: "hold", enabled: true, quarantined: false, robots_disallow: [], notes: "Rights ruling pending; ToS review incomplete" },
  ],
};

/* ── UI Helpers ───────────────────────────────────────────────────────── */

function ContentTypeIcon({ type }: { type: string }) {
  switch (type) {
    case "api":
      return <Globe size={12} strokeWidth={2} />;
    case "pdf":
      return <FileText size={12} strokeWidth={2} />;
    case "html":
      return <Globe size={12} strokeWidth={2} />;
    case "feed":
      return <FileText size={12} strokeWidth={2} />;
    case "file":
      return <FileText size={12} strokeWidth={2} />;
    default:
      return <FileText size={12} strokeWidth={2} />;
  }
}

function SourceClassIcon({ cls }: { cls: string }) {
  switch (cls) {
    case "public":
      return <Globe size={11} strokeWidth={2} />;
    case "authenticated":
      return <Lock size={11} strokeWidth={2} />;
    case "licensed":
      return <Shield size={11} strokeWidth={2} />;
    case "prohibited":
      return <Ban size={11} strokeWidth={2} />;
    case "unclear":
      return <HelpCircle size={11} strokeWidth={2} />;
    default:
      return <HelpCircle size={11} strokeWidth={2} />;
  }
}

function ClassificationBadge({ classification }: { classification: string }) {
  const styles: Record<string, string> = {
    approved: "text-[var(--sr-status-success)] border-[var(--sr-status-success)]/30 bg-[var(--sr-status-success)]/10",
    hold: "text-[var(--sr-status-warning)] border-[var(--sr-status-warning)]/30 bg-[var(--sr-status-warning)]/10",
    blocked: "text-red-400 border-red-400/30 bg-red-400/10",
  };
  const cls = styles[classification] || styles.hold;
  return (
    <span className={`inline-flex items-center gap-1 admin-mono-font text-[9px] uppercase tracking-[0.1em] px-2 py-[3px] rounded border ${cls}`}>
      {classification === "approved" && <CheckCircle2 size={10} strokeWidth={2} />}
      {classification === "hold" && <AlertTriangle size={10} strokeWidth={2} />}
      {classification === "blocked" && <Ban size={10} strokeWidth={2} />}
      {classification}
    </span>
  );
}

/* ── Main Page ────────────────────────────────────────────────────────── */

export default function SourcesPolicyPage() {
  const [policy, setPolicy] = useState<PolicySummary>(STATIC_POLICY);
  const [loading, setLoading] = useState(false);
  const [usingApi, setUsingApi] = useState(false);

  const fetchPolicy = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch(`${API_BASE}/sources/policy`);
      if (res.ok) {
        const data = await res.json();
        setPolicy(data);
        setUsingApi(true);
      } else {
        setPolicy(STATIC_POLICY);
        setUsingApi(false);
      }
    } catch {
      setPolicy(STATIC_POLICY);
      setUsingApi(false);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchPolicy();
  }, [fetchPolicy]);

  return (
    <div className="p-8 max-w-[1400px] mx-auto">
      {/* Header */}
      <div className="mb-6">
        <div className="flex items-center gap-3 mb-1">
          <Shield size={20} className="text-[var(--sr-action)]" strokeWidth={2} />
          <h1 className="admin-header-font text-xl text-[var(--sr-text-primary)]">
            Collection Policy
          </h1>
          <span
            data-testid="issue-label"
            className="admin-mono-font text-[9px] tracking-[0.14em] uppercase text-[var(--sr-marine-200)] border border-[var(--sr-marine-600)]/40 rounded-full px-2 py-[2px]"
          >
            {policy.issue_label}
          </span>
        </div>
        <p className="text-sm text-[var(--sr-text-secondary)]">
          Responsible data collection policy and source registry. Every byte the
          platform collects must reference an approved source record and policy version.
        </p>
      </div>

      {/* Policy metadata cards */}
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3 mb-6">
        <div className="border border-[var(--sr-border-subtle)] rounded-lg p-3 bg-[var(--sr-surface-deep)]">
          <div className="admin-mono-font text-[9px] uppercase tracking-[0.12em] text-[var(--sr-text-tertiary)] mb-1">
            Policy Version
          </div>
          <div data-testid="policy-version" className="admin-mono-font text-sm text-[var(--sr-text-primary)]">
            {policy.version}
          </div>
        </div>
        <div className="border border-[var(--sr-border-subtle)] rounded-lg p-3 bg-[var(--sr-surface-deep)]">
          <div className="admin-mono-font text-[9px] uppercase tracking-[0.12em] text-[var(--sr-text-tertiary)] mb-1">
            Approved Date
          </div>
          <div data-testid="approved-date" className="admin-mono-font text-sm text-[var(--sr-text-primary)]">
            {policy.approved_date}
          </div>
        </div>
        <div className="border border-[var(--sr-border-subtle)] rounded-lg p-3 bg-[var(--sr-surface-deep)]">
          <div className="admin-mono-font text-[9px] uppercase tracking-[0.12em] text-[var(--sr-text-tertiary)] mb-1">
            Authority
          </div>
          <div data-testid="authority-name" className="text-sm text-[var(--sr-text-primary)]">
            {policy.authority}
          </div>
        </div>
        <div className="border border-[var(--sr-border-subtle)] rounded-lg p-3 bg-[var(--sr-surface-deep)]">
          <div className="admin-mono-font text-[9px] uppercase tracking-[0.12em] text-[var(--sr-text-tertiary)] mb-1">
            Spec Reference
          </div>
          <div className="admin-mono-font text-sm text-[var(--sr-text-primary)]">
            {policy.spec_reference}
          </div>
        </div>
        <div className="border border-[var(--sr-border-subtle)] rounded-lg p-3 bg-[var(--sr-surface-deep)] col-span-2">
          <div className="admin-mono-font text-[9px] uppercase tracking-[0.12em] text-[var(--sr-text-tertiary)] mb-1">
            User-Agent
          </div>
          <div data-testid="user-agent" className="admin-mono-font text-xs text-[var(--sr-text-primary)] break-all">
            {policy.user_agent}
          </div>
        </div>
      </div>

      {/* Count summary */}
      <div className="flex items-center gap-4 mb-4">
        <div className="flex items-center gap-2">
          <CheckCircle2 size={14} className="text-[var(--sr-status-success)]" strokeWidth={2} />
          <span className="admin-mono-font text-xs text-[var(--sr-text-secondary)]">
            Approved: <span data-testid="approved-count" className="text-[var(--sr-text-primary)] font-semibold">{policy.counts.approved}</span>
          </span>
        </div>
        <div className="flex items-center gap-2">
          <AlertTriangle size={14} className="text-[var(--sr-status-warning)]" strokeWidth={2} />
          <span className="admin-mono-font text-xs text-[var(--sr-text-secondary)]">
            Hold: <span data-testid="hold-count" className="text-[var(--sr-text-primary)] font-semibold">{policy.counts.hold}</span>
          </span>
        </div>
        <div className="flex items-center gap-2">
          <Ban size={14} className="text-red-400" strokeWidth={2} />
          <span className="admin-mono-font text-xs text-[var(--sr-text-secondary)]">
            Blocked: <span className="text-[var(--sr-text-primary)] font-semibold">{policy.counts.blocked}</span>
          </span>
        </div>
        <div className="flex items-center gap-2 ml-auto">
          <span className="admin-mono-font text-xs text-[var(--sr-text-secondary)]">
            Total: <span data-testid="total-count" className="text-[var(--sr-text-primary)] font-semibold">{policy.counts.total}</span>
          </span>
        </div>
      </div>

      {/* Source table */}
      <div className="border border-[var(--sr-border-subtle)] rounded-lg overflow-hidden bg-[var(--sr-surface-deep)]">
        <table data-testid="source-table" className="w-full">
          <thead>
            <tr className="border-b border-[var(--sr-border-subtle)] bg-[var(--sr-surface-page)]">
              <th className="admin-mono-font text-[9px] uppercase tracking-[0.12em] text-[var(--sr-text-tertiary)] text-left px-4 py-2">
                Source
              </th>
              <th className="admin-mono-font text-[9px] uppercase tracking-[0.12em] text-[var(--sr-text-tertiary)] text-left px-4 py-2">
                Category
              </th>
              <th className="admin-mono-font text-[9px] uppercase tracking-[0.12em] text-[var(--sr-text-tertiary)] text-left px-4 py-2">
                Type
              </th>
              <th className="admin-mono-font text-[9px] uppercase tracking-[0.12em] text-[var(--sr-text-tertiary)] text-left px-4 py-2">
                Class
              </th>
              <th className="admin-mono-font text-[9px] uppercase tracking-[0.12em] text-[var(--sr-text-tertiary)] text-left px-4 py-2">
                Status
              </th>
              <th className="admin-mono-font text-[9px] uppercase tracking-[0.12em] text-[var(--sr-text-tertiary)] text-left px-4 py-2">
                Notes
              </th>
            </tr>
          </thead>
          <tbody>
            {policy.sources.map((src) => (
              <tr
                key={src.slug}
                data-source-slug={src.slug}
                data-source-class={src.source_class}
                data-source-classification={src.classification}
                data-source-content-type={src.content_type}
                className="border-b border-[var(--sr-border-subtle)]/50 hover:bg-[var(--sr-surface-page)]/50 transition-colors"
              >
                <td className="px-4 py-2.5">
                  <div className="text-sm text-[var(--sr-text-primary)]">{src.display_name}</div>
                  <div className="admin-mono-font text-[10px] text-[var(--sr-text-tertiary)]">{src.slug}</div>
                </td>
                <td className="px-4 py-2.5">
                  <span className="admin-mono-font text-[10px] uppercase tracking-[0.08em] text-[var(--sr-text-secondary)]">
                    {src.category}
                  </span>
                </td>
                <td className="px-4 py-2.5">
                  <span className="admin-mono-font text-[10px] uppercase tracking-[0.08em] text-[var(--sr-text-secondary)] flex items-center gap-1.5">
                    <ContentTypeIcon type={src.content_type} />
                    {src.content_type}
                  </span>
                </td>
                <td className="px-4 py-2.5">
                  <span className="admin-mono-font text-[10px] uppercase tracking-[0.08em] text-[var(--sr-text-secondary)] flex items-center gap-1.5">
                    <SourceClassIcon cls={src.source_class} />
                    {src.source_class}
                  </span>
                </td>
                <td className="px-4 py-2.5">
                  <ClassificationBadge classification={src.classification} />
                </td>
                <td className="px-4 py-2.5 max-w-[300px]">
                  <span className="text-xs text-[var(--sr-text-tertiary)] truncate block">
                    {src.notes || "—"}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

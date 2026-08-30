import type { Metadata } from "next";
import MainNav from "@/components/MainNav";
import EditorialFooter from "@/components/EditorialFooter";

export const metadata: Metadata = {
  title: "Data Sources & Collection Policy",
  description:
    "SailRatings responsible data collection policy: source classifications, robots.txt compliance, rate limiting, takedown procedures, and retention rules.",
  alternates: { canonical: "/sources" },
  robots: { index: false },
};

/**
 * DP-01-02 — Responsible Collection Policy public page.
 *
 * This page mirrors the CollectionPolicyDecisionV1 contract defined in the
 * Python backend (src/irc_data/sources/policy.py).  The data below is kept
 * in sync with the interim-v0 policy approved 2026-08-30.
 *
 * The page is deliberately a static server component — no API calls — so it
 * is always available even when the backend is down.  E2E Playwright tests
 * verify the policy is correctly surfaced here.
 */

const POLICY_VERSION = "interim-v0";
const POLICY_APPROVED = "2026-08-30";
const POLICY_AUTHORITY = "Stuart McLeod, SailRatings founder";
const USER_AGENT =
  "SailRatings/1.0 (+https://sailratings.com; contact=stuart@sailratings.com)";

type SourceClass =
  | "public"
  | "authenticated"
  | "licensed"
  | "prohibited"
  | "unclear";

type LegalStatus = "approved" | "hold" | "blocked";

interface SourceEntry {
  slug: string;
  name: string;
  category: string;
  sourceClass: SourceClass;
  legalStatus: LegalStatus;
  rationale: string;
}

const SOURCES: SourceEntry[] = [
  {
    slug: "sailsys",
    name: "SailSys",
    category: "results",
    sourceClass: "public",
    legalStatus: "approved",
    rationale: "Australian race management; publicly published results",
  },
  {
    slug: "topyacht",
    name: "TopYacht",
    category: "results",
    sourceClass: "public",
    legalStatus: "approved",
    rationale: "Australian race management; publicly published results",
  },
  {
    slug: "irc-tcc",
    name: "IRC TCC Listings",
    category: "ratings",
    sourceClass: "public",
    legalStatus: "approved",
    rationale:
      "Published for racing administration; CSV download from ircrating.org",
  },
  {
    slug: "orc",
    name: "ORC",
    category: "ratings",
    sourceClass: "public",
    legalStatus: "approved",
    rationale: "Published for racing administration; JSON API from data.orc.org",
  },
  {
    slug: "yachtscoring",
    name: "Yacht Scoring",
    category: "results",
    sourceClass: "public",
    legalStatus: "approved",
    rationale: "US/international race results; publicly published",
  },
  {
    slug: "manage2sail",
    name: "Manage2Sail",
    category: "results",
    sourceClass: "public",
    legalStatus: "approved",
    rationale: "European race management; publicly published results",
  },
  {
    slug: "sailwave",
    name: "Sailwave",
    category: "results",
    sourceClass: "public",
    legalStatus: "approved",
    rationale: "Results files publicly linked from club sites",
  },
  {
    slug: "sailing-news",
    name: "Sailing News Feeds",
    category: "news",
    sourceClass: "public",
    legalStatus: "approved",
    rationale: "RSS/Atom feeds; explicitly published for syndication",
  },
  {
    slug: "irc-certs",
    name: "IRC Certificate PDFs",
    category: "certificates",
    sourceClass: "public",
    legalStatus: "approved",
    rationale:
      "Publicly accessible; published for racing administration; core platform data",
  },
  {
    slug: "clubspot",
    name: "ClubSpot",
    category: "results",
    sourceClass: "unclear",
    legalStatus: "hold",
    rationale: "Rights ruling pending; ToS review incomplete",
  },
  {
    slug: "kwindoo",
    name: "Kwindoo",
    category: "results",
    sourceClass: "unclear",
    legalStatus: "hold",
    rationale: "Rights ruling pending; ToS review incomplete",
  },
];

const RULES = [
  {
    title: "robots.txt compliance",
    icon: "🤖",
    rules: [
      "Fetch and parse robots.txt at the start of every collection session",
      "Cache the parsed disallow list; re-fetch if older than 24 hours",
      "Skip any URL path that matches a disallow rule",
      "A 404 on robots.txt means no rules — proceed normally",
      "If robots.txt cannot be fetched (5xx): stop collection and create an incident",
    ],
  },
  {
    title: "Rate limiting",
    icon: "⏱️",
    rules: [
      "Maximum 1 request per 2 seconds per domain",
      "Apply jitter: actual delay = 2.0s + random(0, 1.0s)",
      "Honour Retry-After headers on 429 responses",
      "Exponential backoff on 5xx: 2s → 4s → 8s → 16s → abort",
    ],
  },
  {
    title: "Collection window",
    icon: "🌙",
    rules: [
      "Nightly only: 01:00–06:00 source-local time where timezone is known",
      "For sources with unknown timezone: use UTC 01:00–06:00",
      "No daytime scraping except for on-demand health checks",
      "Exception: SailSys results every 30 min (lightweight published feed)",
    ],
  },
  {
    title: "Conditional requests & deduplication",
    icon: "🔄",
    rules: [
      "Send If-None-Match / If-Modified-Since on repeat fetches",
      "Treat 304 Not Modified as a clean success — no re-download",
      "SHA-256 hash every response body before storage",
      "Skip storage if hash matches the last stored artifact for that URL",
    ],
  },
  {
    title: "Hard caps per source per night",
    icon: "📊",
    rules: [
      "Maximum 25 MB per individual downloaded object",
      "Maximum 5,000 HTTP fetches per source per nightly run",
      "Maximum 500 MB total download per source per night",
    ],
  },
  {
    title: "Prohibited collection",
    icon: "🚫",
    rules: [
      "No login walls — do not submit credentials to access gated content",
      "No paywalls — do not circumvent subscription or payment gates",
      "No CAPTCHA bypass — no solvers, proxies, or human relay",
      "No personal data beyond published results",
      "No session hijacking, token reuse, or auth header manipulation",
    ],
  },
];

const PERSONAL_DATA_PROHIBITED = [
  "owner_name",
  "owner_email",
  "owner_phone",
  "owner_address",
  "home_port",
  "financial_data",
];

const classBadgeClass: Record<SourceClass, string> = {
  public: "bg-emerald-500/15 text-emerald-400 border-emerald-500/30",
  authenticated: "bg-blue-500/15 text-blue-400 border-blue-500/30",
  licensed: "bg-purple-500/15 text-purple-400 border-purple-500/30",
  prohibited: "bg-red-500/15 text-red-400 border-red-500/30",
  unclear: "bg-amber-500/15 text-amber-400 border-amber-500/30",
};

const statusBadgeClass: Record<LegalStatus, string> = {
  approved: "bg-emerald-500/15 text-emerald-400 border-emerald-500/30",
  hold: "bg-amber-500/15 text-amber-400 border-amber-500/30",
  blocked: "bg-red-500/15 text-red-400 border-red-500/30",
};

export default function SourcesPage() {
  const approvedSources = SOURCES.filter((s) => s.legalStatus === "approved");
  const holdSources = SOURCES.filter((s) => s.legalStatus === "hold");

  return (
    <div className="min-h-screen bg-[var(--sr-surface-page)]">
      <MainNav />

      <main className="max-w-4xl mx-auto px-6 py-16">
        {/* ─── Header ─── */}
        <div className="mb-12">
          <p
            className="text-[11px] uppercase tracking-[0.2em] text-[var(--sr-text-tertiary)] mb-3"
            data-testid="policy-page-label"
          >
            DP-01-02 · Responsible Collection
          </p>
          <h1 className="font-[var(--sr-font-display)] text-4xl font-bold text-[var(--sr-text-primary)] mb-4 tracking-tight">
            Data Sources &amp; Collection Policy
          </h1>
          <p className="text-[var(--sr-text-secondary)] text-lg leading-relaxed">
            Every byte the SailRatings platform collects is governed by this
            policy. It classifies sources, enforces robots.txt compliance, rate
            limits, attribution, takedown response, and retention rules.
          </p>
        </div>

        {/* ─── Policy version banner ─── */}
        <div
          className="border border-[var(--sr-border-strong)] rounded-lg p-6 mb-12 bg-[var(--sr-surface-card)]"
          data-testid="policy-version-banner"
        >
          <div className="flex flex-wrap items-center gap-6">
            <div>
              <p className="text-[10px] uppercase tracking-[0.16em] text-[var(--sr-text-tertiary)] mb-1">
                Policy Version
              </p>
              <p
                className="font-[var(--sr-font-data)] text-xl text-[var(--sr-text-primary)]"
                data-testid="policy-version"
              >
                {POLICY_VERSION}
              </p>
            </div>
            <div className="h-10 w-px bg-[var(--sr-border-subtle)]" />
            <div>
              <p className="text-[10px] uppercase tracking-[0.16em] text-[var(--sr-text-tertiary)] mb-1">
                Approved
              </p>
              <p
                className="font-[var(--sr-font-data)] text-xl text-[var(--sr-text-primary)]"
                data-testid="policy-approved-date"
              >
                {POLICY_APPROVED}
              </p>
            </div>
            <div className="h-10 w-px bg-[var(--sr-border-subtle)]" />
            <div>
              <p className="text-[10px] uppercase tracking-[0.16em] text-[var(--sr-text-tertiary)] mb-1">
                Authority
              </p>
              <p className="text-sm text-[var(--sr-text-primary)]">
                {POLICY_AUTHORITY}
              </p>
            </div>
          </div>
          <div className="mt-4 pt-4 border-t border-[var(--sr-border-subtle)]">
            <p className="text-[10px] uppercase tracking-[0.16em] text-[var(--sr-text-tertiary)] mb-1">
              User-Agent
            </p>
            <p
              className="font-[var(--sr-font-data)] text-xs text-[var(--sr-text-secondary)] break-all"
              data-testid="user-agent"
            >
              {USER_AGENT}
            </p>
          </div>
        </div>

        {/* ─── Source classification table ─── */}
        <section className="mb-12" data-testid="source-classification-section">
          <h2 className="text-xl font-semibold text-[var(--sr-text-primary)] mb-1">
            Source Classification
          </h2>
          <p className="text-sm text-[var(--sr-text-tertiary)] mb-6">
            Each source is classified as{" "}
            <span className="text-emerald-400">public</span>,{" "}
            <span className="text-blue-400">authenticated</span>,{" "}
            <span className="text-purple-400">licensed</span>,{" "}
            <span className="text-red-400">prohibited</span>, or{" "}
            <span className="text-amber-400">unclear</span>.
          </p>

          <div className="overflow-x-auto">
            <table className="w-full text-sm" data-testid="source-table">
              <thead>
                <tr className="border-b border-[var(--sr-border-strong)]">
                  <th className="text-left py-3 px-3 text-[10px] uppercase tracking-[0.12em] text-[var(--sr-text-tertiary)]">
                    Source
                  </th>
                  <th className="text-left py-3 px-3 text-[10px] uppercase tracking-[0.12em] text-[var(--sr-text-tertiary)]">
                    Category
                  </th>
                  <th className="text-left py-3 px-3 text-[10px] uppercase tracking-[0.12em] text-[var(--sr-text-tertiary)]">
                    Class
                  </th>
                  <th className="text-left py-3 px-3 text-[10px] uppercase tracking-[0.12em] text-[var(--sr-text-tertiary)]">
                    Status
                  </th>
                  <th className="text-left py-3 px-3 text-[10px] uppercase tracking-[0.12em] text-[var(--sr-text-tertiary)]">
                    Rationale
                  </th>
                </tr>
              </thead>
              <tbody>
                {SOURCES.map((src) => (
                  <tr
                    key={src.slug}
                    className="border-b border-[var(--sr-border-subtle)] hover:bg-[var(--sr-surface-interactive)] transition-colors"
                    data-testid={`source-row-${src.slug}`}
                    data-source-slug={src.slug}
                    data-source-class={src.sourceClass}
                    data-legal-status={src.legalStatus}
                  >
                    <td className="py-3 px-3">
                      <div className="text-[var(--sr-text-primary)] font-medium">
                        {src.name}
                      </div>
                      <div className="font-[var(--sr-font-data)] text-[10px] text-[var(--sr-text-tertiary)]">
                        {src.slug}
                      </div>
                    </td>
                    <td className="py-3 px-3 text-[var(--sr-text-secondary)] capitalize">
                      {src.category}
                    </td>
                    <td className="py-3 px-3">
                      <span
                        className={`inline-block px-2 py-0.5 rounded text-[10px] uppercase tracking-wide border ${classBadgeClass[src.sourceClass]}`}
                        data-testid={`source-class-${src.slug}`}
                      >
                        {src.sourceClass}
                      </span>
                    </td>
                    <td className="py-3 px-3">
                      <span
                        className={`inline-block px-2 py-0.5 rounded text-[10px] uppercase tracking-wide border ${statusBadgeClass[src.legalStatus]}`}
                        data-testid={`legal-status-${src.slug}`}
                      >
                        {src.legalStatus}
                      </span>
                    </td>
                    <td className="py-3 px-3 text-[var(--sr-text-tertiary)] text-xs max-w-xs">
                      {src.rationale}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="mt-4 flex gap-6 text-xs text-[var(--sr-text-tertiary)]">
            <span data-testid="approved-count">
              {approvedSources.length} approved
            </span>
            <span data-testid="hold-count">
              {holdSources.length} on hold
            </span>
            <span data-testid="total-count">
              {SOURCES.length} total sources
            </span>
          </div>
        </section>

        {/* ─── Collection rules ─── */}
        <section className="mb-12" data-testid="collection-rules-section">
          <h2 className="text-xl font-semibold text-[var(--sr-text-primary)] mb-6">
            Collection Rules
          </h2>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {RULES.map((ruleBlock) => (
              <div
                key={ruleBlock.title}
                className="border border-[var(--sr-border-strong)] rounded-lg p-5 bg-[var(--sr-surface-card)]"
                data-testid={`rule-block-${ruleBlock.title
                  .toLowerCase()
                  .replace(/[^a-z0-9]+/g, "-")}`}
              >
                <h3 className="text-sm font-semibold text-[var(--sr-text-primary)] mb-3 flex items-center gap-2">
                  <span>{ruleBlock.icon}</span>
                  {ruleBlock.title}
                </h3>
                <ul className="space-y-1.5">
                  {ruleBlock.rules.map((rule, i) => (
                    <li
                      key={i}
                      className="text-xs text-[var(--sr-text-secondary)] leading-relaxed flex gap-2"
                    >
                      <span className="text-[var(--sr-text-tertiary)]">›</span>
                      <span>{rule}</span>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </section>

        {/* ─── Personal data ─── */}
        <section className="mb-12" data-testid="personal-data-section">
          <h2 className="text-xl font-semibold text-[var(--sr-text-primary)] mb-3">
            Personal Data Restrictions
          </h2>
          <p className="text-sm text-[var(--sr-text-secondary)] mb-4">
            The following fields are prohibited from collection — no personal
            data beyond published results is harvested.
          </p>
          <div className="flex flex-wrap gap-2">
            {PERSONAL_DATA_PROHIBITED.map((field) => (
              <span
                key={field}
                className="inline-block px-3 py-1 rounded text-xs font-[var(--sr-font-data)] border border-red-500/30 bg-red-500/10 text-red-400"
                data-testid={`prohibited-field-${field}`}
              >
                {field}
              </span>
            ))}
          </div>
        </section>

        {/* ─── Takedown & kill switch ─── */}
        <section className="mb-12" data-testid="takedown-section">
          <h2 className="text-xl font-semibold text-[var(--sr-text-primary)] mb-3">
            Takedown &amp; Emergency Disable
          </h2>
          <div className="border border-[var(--sr-border-strong)] rounded-lg p-5 bg-[var(--sr-surface-card)] space-y-3">
            <div>
              <p className="text-[10px] uppercase tracking-[0.12em] text-[var(--sr-text-tertiary)] mb-1">
                Response Window
              </p>
              <p className="text-sm text-[var(--sr-text-primary)]" data-testid="takedown-window">
                Within 4 hours of contact: disable source, quarantine captures,
                acknowledge receipt.
              </p>
            </div>
            <div>
              <p className="text-[10px] uppercase tracking-[0.12em] text-[var(--sr-text-tertiary)] mb-1">
                Kill Switch
              </p>
              <p className="text-sm text-[var(--sr-text-primary)]" data-testid="kill-switch-desc">
                Emergency disable works by source slug and by domain — either
                dimension blocks collection immediately. A global kill switch
                (COLLECTION_ENABLED=false) halts all collection across all
                sources without DB changes.
              </p>
            </div>
            <div>
              <p className="text-[10px] uppercase tracking-[0.12em] text-[var(--sr-text-tertiary)] mb-1">
                Contact
              </p>
              <p className="text-sm text-[var(--sr-text-primary)]" data-testid="takedown-contact">
                stuart@sailratings.com
              </p>
            </div>
          </div>
        </section>

        {/* ─── Enforcement ─── */}
        <section className="mb-12" data-testid="enforcement-section">
          <h2 className="text-xl font-semibold text-[var(--sr-text-primary)] mb-3">
            Enforcement
          </h2>
          <p className="text-sm text-[var(--sr-text-secondary)] leading-relaxed">
            The collection gate (CollectionGate) is the single enforcement
            point every adapter must pass through before issuing HTTP requests.
            It enforces policy version, source approval, emergency disable,
            robots.txt compliance, source classification, collection window, and
            rate limiting. An adapter cannot run without an approved policy
            version — a version mismatch raises{" "}
            <code className="font-[var(--sr-font-data)] text-xs text-[var(--sr-text-primary)] bg-[var(--sr-surface-interactive)] px-1.5 py-0.5 rounded">
              PolicyVersionMismatchError
            </code>{" "}
            and aborts immediately.
          </p>
        </section>
      </main>

      <EditorialFooter />
    </div>
  );
}

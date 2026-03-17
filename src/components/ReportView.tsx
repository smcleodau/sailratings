"use client";

import { Download, Trophy, Target, Swords } from "lucide-react";
import { getReportPdfUrl, type ReportData } from "@/lib/api";

interface ReportViewProps {
  report: ReportData;
  token: string;
}

export default function ReportView({ report, token }: ReportViewProps) {
  const { boat, report_markdown, recommendations, rai, rivals } = report;

  return (
    <div className="max-w-3xl mx-auto px-6 py-12 space-y-12">
      {/* Header */}
      {boat && (
        <header className="border border-border bg-white px-8 py-8 sm:px-12 sm:py-10">
          <div className="flex items-start justify-between gap-4 mb-4">
            <div>
              <h1 className="heading-display text-3xl sm:text-4xl text-ink">
                {boat.boat_name}
              </h1>
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
                {boat.country && (
                  <>
                    <span className="text-border">|</span>
                    <span className="body-text text-sm text-muted">
                      {boat.country}
                    </span>
                  </>
                )}
              </div>
            </div>
            {boat.irc_tcc != null && (
              <div className="text-right flex-shrink-0">
                <div className="data-mono text-3xl sm:text-4xl text-navy font-semibold">
                  {Number(boat.irc_tcc).toFixed(3)}
                </div>
                <div className="text-xs text-muted uppercase tracking-wider mt-1">
                  TCC
                </div>
              </div>
            )}
          </div>

          {/* Download PDF */}
          <div className="mt-6 pt-6 border-t border-border-light flex items-center justify-between">
            <span className="body-text text-sm text-muted">
              Full IRC Rating Analysis
            </span>
            <a
              href={getReportPdfUrl(token)}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-2 text-brass hover:text-brass-dark transition-colors font-body text-sm font-medium"
            >
              <Download size={16} strokeWidth={1.5} />
              Download PDF
            </a>
          </div>
        </header>
      )}

      {/* Analysis prose */}
      {report_markdown && (
        <section className="border border-border bg-white px-8 py-8 sm:px-12 sm:py-10">
          <div
            className="prose-report"
            dangerouslySetInnerHTML={{ __html: markdownToHtml(report_markdown) }}
          />
        </section>
      )}

      {/* Recommendations table */}
      {recommendations && recommendations.length > 0 && (
        <section className="border border-border bg-white">
          <div className="border-b border-border-light px-8 py-5 flex items-center gap-3">
            <Target size={18} strokeWidth={1.5} className="text-brass" />
            <h2 className="heading-display text-xl text-ink">
              Optimisation Recommendations
            </h2>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-left">
              <thead>
                <tr className="border-b border-border-light text-xs uppercase tracking-wider text-muted">
                  <th className="px-6 py-3 font-medium">#</th>
                  <th className="px-6 py-3 font-medium">Field</th>
                  <th className="px-6 py-3 font-medium">Category</th>
                  <th className="px-6 py-3 font-medium text-right">Current</th>
                  <th className="px-6 py-3 font-medium text-right">Mean</th>
                  <th className="px-6 py-3 font-medium text-right">
                    TCC Delta
                  </th>
                  <th className="px-6 py-3 font-medium">Feasibility</th>
                </tr>
              </thead>
              <tbody>
                {recommendations.map((rec, i) => (
                  <tr
                    key={i}
                    className={`border-b border-border-light last:border-b-0 ${
                      i % 2 === 0 ? "bg-white" : "bg-cream/50"
                    }`}
                  >
                    <td className="px-6 py-4">
                      <span className="data-mono text-sm text-brass font-semibold">
                        {rec.rank}
                      </span>
                    </td>
                    <td className="px-6 py-4">
                      <span className="body-text text-sm text-ink font-medium">
                        {rec.field}
                      </span>
                    </td>
                    <td className="px-6 py-4">
                      <span className="body-text text-xs text-muted">
                        {rec.category}
                      </span>
                    </td>
                    <td className="px-6 py-4 text-right">
                      <span className="data-mono text-sm text-ink">
                        {formatValue(rec.current_value)}
                      </span>
                    </td>
                    <td className="px-6 py-4 text-right">
                      <span className="data-mono text-sm text-muted">
                        {formatValue(rec.mean_value)}
                      </span>
                    </td>
                    <td className="px-6 py-4 text-right">
                      <span
                        className={`data-mono text-sm font-semibold ${
                          rec.tcc_delta < 0 ? "text-navy" : "text-brass"
                        }`}
                      >
                        {rec.tcc_delta > 0 ? "+" : ""}
                        {Number(rec.tcc_delta).toFixed(4)}
                      </span>
                    </td>
                    <td className="px-6 py-4">
                      <span className="body-text text-xs text-muted">
                        {rec.feasibility}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {/* Expandable explanations */}
          <div className="border-t border-border-light px-8 py-6 space-y-4">
            {recommendations.map((rec, i) => (
              <div key={i}>
                <div className="flex items-baseline gap-2 mb-1">
                  <span className="data-mono text-xs text-brass font-semibold">
                    {rec.rank}.
                  </span>
                  <span className="body-text text-sm text-ink font-medium">
                    {rec.field}
                  </span>
                  {rec.evidence_strength && (
                    <span className="data-mono text-[10px] text-muted uppercase">
                      {rec.evidence_strength}
                    </span>
                  )}
                </div>
                <p className="body-text text-sm text-ink-light pl-6">
                  {rec.explanation}
                </p>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* RAI card */}
      {rai && (
        <section className="border border-border bg-white">
          <div className="border-b border-border-light px-8 py-5 flex items-center gap-3">
            <Trophy size={18} strokeWidth={1.5} className="text-brass" />
            <h2 className="heading-display text-xl text-ink">
              Racing Performance Index
            </h2>
          </div>
          <div className="px-8 py-8 sm:px-12">
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-6 mb-6">
              <StatCard label="RAI Score" value={rai.rai_score.toFixed(2)} />
              <StatCard label="Races" value={String(rai.n_races)} />
              <StatCard label="Wins" value={String(rai.wins)} />
              <StatCard label="Podiums" value={String(rai.podiums)} />
            </div>
            <div className="flex items-center gap-2 mb-4">
              <span className="data-mono text-xs text-muted">
                95% CI: {rai.ci_low.toFixed(2)} &ndash;{" "}
                {rai.ci_high.toFixed(2)}
              </span>
            </div>
            {rai.interpretation && (
              <p className="body-text text-sm text-ink-light">
                {rai.interpretation}
              </p>
            )}
          </div>
        </section>
      )}

      {/* Rivals table */}
      {rivals && rivals.length > 0 && (
        <section className="border border-border bg-white">
          <div className="border-b border-border-light px-8 py-5 flex items-center gap-3">
            <Swords size={18} strokeWidth={1.5} className="text-brass" />
            <h2 className="heading-display text-xl text-ink">
              Head-to-Head Rivals
            </h2>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-left">
              <thead>
                <tr className="border-b border-border-light text-xs uppercase tracking-wider text-muted">
                  <th className="px-6 py-3 font-medium">Boat</th>
                  <th className="px-6 py-3 font-medium">Sail #</th>
                  <th className="px-6 py-3 font-medium text-right">W</th>
                  <th className="px-6 py-3 font-medium text-right">L</th>
                  <th className="px-6 py-3 font-medium text-right">Win %</th>
                  <th className="px-6 py-3 font-medium text-right">Events</th>
                </tr>
              </thead>
              <tbody>
                {rivals.map((rival, i) => (
                  <tr
                    key={i}
                    className={`border-b border-border-light last:border-b-0 ${
                      i % 2 === 0 ? "bg-white" : "bg-cream/50"
                    }`}
                  >
                    <td className="px-6 py-4">
                      <span className="body-text text-sm text-ink font-medium">
                        {rival.boat_name}
                      </span>
                    </td>
                    <td className="px-6 py-4">
                      <span className="data-mono text-xs text-muted">
                        {rival.sail_number}
                      </span>
                    </td>
                    <td className="px-6 py-4 text-right">
                      <span className="data-mono text-sm text-navy">
                        {rival.wins}
                      </span>
                    </td>
                    <td className="px-6 py-4 text-right">
                      <span className="data-mono text-sm text-ink">
                        {rival.losses}
                      </span>
                    </td>
                    <td className="px-6 py-4 text-right">
                      <span className="data-mono text-sm text-ink font-medium">
                        {(rival.win_rate * 100).toFixed(0)}%
                      </span>
                    </td>
                    <td className="px-6 py-4 text-right">
                      <span className="data-mono text-sm text-muted">
                        {rival.events}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  );
}

/* ── Helpers ──────────────────────────────────────────────────────────── */

function StatCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="text-center">
      <div className="data-mono text-2xl text-ink font-semibold">{value}</div>
      <div className="text-xs text-muted uppercase tracking-wider mt-1">
        {label}
      </div>
    </div>
  );
}

function formatValue(val: number | string): string {
  if (typeof val === "number") return val.toFixed(3);
  return String(val);
}

function markdownToHtml(md: string): string {
  return md
    .replace(/^### (.+)$/gm, '<h3 class="heading-display text-lg text-ink mt-8 mb-3">$1</h3>')
    .replace(/^## (.+)$/gm, '<h2 class="heading-display text-xl text-ink mt-10 mb-4">$1</h2>')
    .replace(/^# (.+)$/gm, '<h1 class="heading-display text-2xl text-ink mt-10 mb-4">$1</h1>')
    .replace(/\*\*(.+?)\*\*/g, '<strong class="font-semibold text-ink">$1</strong>')
    .replace(/\*(.+?)\*/g, "<em>$1</em>")
    .replace(/\n\n/g, '</p><p class="body-text text-ink-light text-base leading-relaxed mb-4">')
    .replace(/^/, '<p class="body-text text-ink-light text-base leading-relaxed mb-4">')
    .replace(/$/, "</p>");
}

"use client";

import { useEffect, useState } from "react";
import { Download, Trophy, Target, Swords } from "lucide-react";
import { getReportPdfUrl, type ReportData } from "@/lib/api";
import PinnedMasthead from "@/components/PinnedMasthead";

interface ReportViewProps {
  report: ReportData;
  token: string;
}

function useStreamedMarkdown(fullText: string | undefined, msPerParagraph = 280, startAfterMs = 0) {
  const [revealed, setRevealed] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const [isComplete, setIsComplete] = useState(false);

  useEffect(() => {
    if (!fullText) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setRevealed("");
      setIsStreaming(false);
      setIsComplete(false);
      return;
    }

    const paragraphs = fullText.split(/\n\n+/).filter((p) => p.trim());
    if (paragraphs.length === 0) {
      setRevealed(fullText);
      setIsComplete(true);
      return;
    }

    let cancelled = false;
    let i = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    setRevealed("");
    setIsStreaming(true);
    setIsComplete(false);

    const tick = () => {
      if (cancelled) return;
      i += 1;
      setRevealed(paragraphs.slice(0, i).join("\n\n"));
      if (i >= paragraphs.length) {
        setIsStreaming(false);
        setIsComplete(true);
        return;
      }
      timer = setTimeout(tick, msPerParagraph);
    };

    timer = setTimeout(tick, startAfterMs);

    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [fullText, msPerParagraph, startAfterMs]);

  return { revealed, isStreaming, isComplete };
}

export default function ReportView({ report, token }: ReportViewProps) {
  const { boat, report_markdown, recommendations, rai, rivals } = report;
  const [phase, setPhase] = useState<"compiling" | "table" | "prose">("compiling");

  // 600ms "File compiled. Opening…" beat, then draw the recommendations table.
  useEffect(() => {
    const t1 = setTimeout(() => setPhase("table"), 600);
    const t2 = setTimeout(() => setPhase("prose"), 1400);
    return () => {
      clearTimeout(t1);
      clearTimeout(t2);
    };
  }, []);

  const tableReady = phase === "table" || phase === "prose";
  const proseReady = phase === "prose";

  const { revealed, isStreaming, isComplete: proseDone } = useStreamedMarkdown(
    proseReady && report_markdown ? report_markdown : undefined,
    280,
    0,
  );

  return (
    <>
      {boat && (
        <PinnedMasthead
          boat={boat}
          subline={
            phase === "compiling" ? (
              <span className="text-brass">File compiled. Opening…</span>
            ) : null
          }
        />
      )}

      <div className="max-w-3xl mx-auto px-6 py-10 space-y-12">
        {boat && (
          <div className="flex items-center justify-end">
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
        )}

        {tableReady && recommendations && recommendations.length > 0 && (
          <section className="border border-border bg-white animate-in">
            <div className="border-b border-border-light px-8 py-5 flex items-center gap-3">
              <Target size={18} strokeWidth={1.5} className="text-brass" />
              <h2 className="heading-display text-xl text-charcoal">
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
                    <th className="px-6 py-3 font-medium text-right">TCC Delta</th>
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
                        <span className="data-mono text-sm text-brass font-semibold">{rec.rank}</span>
                      </td>
                      <td className="px-6 py-4">
                        <span className="body-text text-sm text-charcoal font-medium">{rec.field}</span>
                      </td>
                      <td className="px-6 py-4">
                        <span className="body-text text-xs text-muted">{rec.category}</span>
                      </td>
                      <td className="px-6 py-4 text-right">
                        <span className="data-mono text-sm text-charcoal">{formatValue(rec.current_value)}</span>
                      </td>
                      <td className="px-6 py-4 text-right">
                        <span className="data-mono text-sm text-muted">{formatValue(rec.mean_value)}</span>
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
                        <span className="body-text text-xs text-muted">{rec.feasibility}</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="border-t border-border-light px-8 py-6 space-y-4">
              {recommendations.map((rec, i) => (
                <div key={i}>
                  <div className="flex items-baseline gap-2 mb-1">
                    <span className="data-mono text-xs text-brass font-semibold">{rec.rank}.</span>
                    <span className="body-text text-sm text-charcoal font-medium">{rec.field}</span>
                    {rec.evidence_strength && (
                      <span className="data-mono text-[10px] text-muted uppercase">{rec.evidence_strength}</span>
                    )}
                  </div>
                  <p className="body-text text-sm text-charcoal-light pl-6">{rec.explanation}</p>
                </div>
              ))}
            </div>
          </section>
        )}

        {proseReady && report_markdown && (
          <section className="border border-border bg-white px-8 py-8 sm:px-12 sm:py-10 animate-in">
            <div
              className="prose-report"
              dangerouslySetInnerHTML={{ __html: markdownToHtml(revealed) }}
            />
            {isStreaming && (
              <span
                className="inline-block w-0.5 h-5 bg-brass align-text-bottom streaming-pulse ml-0.5"
                aria-hidden="true"
              />
            )}
          </section>
        )}

        {proseDone && rai && (
          <section className="border border-border bg-white animate-in">
            <div className="border-b border-border-light px-8 py-5 flex items-center gap-3">
              <Trophy size={18} strokeWidth={1.5} className="text-brass" />
              <h2 className="heading-display text-xl text-charcoal">Racing Performance Index</h2>
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
                  95% CI: {rai.ci_low.toFixed(2)} &ndash; {rai.ci_high.toFixed(2)}
                </span>
              </div>
              {rai.interpretation && (
                <p className="body-text text-sm text-charcoal-light">{rai.interpretation}</p>
              )}
            </div>
          </section>
        )}

        {proseDone && rivals && rivals.length > 0 && (
          <section className="border border-border bg-white animate-in">
            <div className="border-b border-border-light px-8 py-5 flex items-center gap-3">
              <Swords size={18} strokeWidth={1.5} className="text-brass" />
              <h2 className="heading-display text-xl text-charcoal">Head-to-Head Rivals</h2>
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
                        <span className="body-text text-sm text-charcoal font-medium">{rival.boat_name}</span>
                      </td>
                      <td className="px-6 py-4">
                        <span className="data-mono text-xs text-muted">{rival.sail_number}</span>
                      </td>
                      <td className="px-6 py-4 text-right">
                        <span className="data-mono text-sm text-navy">{rival.wins}</span>
                      </td>
                      <td className="px-6 py-4 text-right">
                        <span className="data-mono text-sm text-charcoal">{rival.losses}</span>
                      </td>
                      <td className="px-6 py-4 text-right">
                        <span className="data-mono text-sm text-charcoal font-medium">
                          {(rival.win_rate * 100).toFixed(0)}%
                        </span>
                      </td>
                      <td className="px-6 py-4 text-right">
                        <span className="data-mono text-sm text-muted">{rival.events}</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        )}
      </div>
    </>
  );
}

function StatCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="text-center">
      <div className="data-mono text-2xl text-charcoal font-semibold">{value}</div>
      <div className="text-xs text-muted uppercase tracking-wider mt-1">{label}</div>
    </div>
  );
}

function formatValue(val: number | string): string {
  if (typeof val === "number") return val.toFixed(3);
  return String(val);
}

function markdownToHtml(md: string): string {
  return md
    .replace(/^### (.+)$/gm, '<h3 class="heading-display text-lg text-charcoal mt-8 mb-3">$1</h3>')
    .replace(/^## (.+)$/gm, '<h2 class="heading-display text-xl text-charcoal mt-10 mb-4">$1</h2>')
    .replace(/^# (.+)$/gm, '<h1 class="heading-display text-2xl text-charcoal mt-10 mb-4">$1</h1>')
    .replace(/\*\*(.+?)\*\*/g, '<strong class="font-semibold text-charcoal">$1</strong>')
    .replace(/\*(.+?)\*/g, "<em>$1</em>")
    .replace(/\n\n/g, '</p><p class="body-text text-charcoal-light text-base leading-relaxed mb-4">')
    .replace(/^/, '<p class="body-text text-charcoal-light text-base leading-relaxed mb-4">')
    .replace(/$/, "</p>");
}

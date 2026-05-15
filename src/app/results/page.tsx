import type { Metadata } from "next";
import MainNav from "@/components/MainNav";
import FunnelCTA from "@/components/FunnelCTA";
import EditorialFooter from "@/components/EditorialFooter";

export const metadata: Metadata = {
  title: "IRC and ORC race results, properly read",
  description:
    "Every corrected finish your boat has sailed under IRC and ORC, joined to the certificate she carried that day. The truth-test of your rating.",
  alternates: { canonical: "/results" },
  openGraph: {
    title: "Results don't lie. Ratings sometimes do.",
    description:
      "Where every IRC and ORC corrected finish meets the certificate that produced it — and where the Racing Advantage Index begins.",
    url: "https://sailratings.com/results",
    type: "article",
  },
};

const jsonLd = {
  "@context": "https://schema.org",
  "@graph": [
    {
      "@type": "BreadcrumbList",
      itemListElement: [
        { "@type": "ListItem", position: 1, name: "Home", item: "https://sailratings.com/" },
        { "@type": "ListItem", position: 2, name: "Race Results", item: "https://sailratings.com/results" },
      ],
    },
    {
      "@type": "Article",
      headline: "IRC and ORC race results, properly read",
      description:
        "Every corrected finish your boat has sailed under IRC and ORC, joined to the certificate she carried that day. The truth-test of your rating.",
      author: { "@type": "Organization", name: "Sail Ratings" },
      publisher: {
        "@type": "Organization",
        name: "Sail Ratings",
        url: "https://sailratings.com",
      },
      mainEntityOfPage: "https://sailratings.com/results",
    },
    {
      "@type": "FAQPage",
      mainEntity: [
        {
          "@type": "Question",
          name: "Where do the race results come from?",
          acceptedAnswer: {
            "@type": "Answer",
            text: "Public scoring outputs from the Royal Ocean Racing Club, the Offshore Racing Congress, SailSys-hosted club series, national authorities and event organising authorities. We do not republish protected data, we do not scrape behind logins, and we credit source organisations on every event page.",
          },
        },
        {
          "@type": "Question",
          name: "How current is the race-results data?",
          acceptedAnswer: {
            "@type": "Answer",
            text: "SailSys results refresh every thirty minutes. ORC certificate scrapes run nightly at 03:00 UTC. IRC certificate discovery and parsing runs weekly. Major-event corrected times typically appear within hours of the organising authority posting them.",
          },
        },
        {
          "@type": "Question",
          name: "Can I correct an error in my boat's record?",
          acceptedAnswer: {
            "@type": "Answer",
            text: "Yes. The pencil-icon next to any masthead field on the Bench submits a correction into our moderation queue, reviewed and merged daily.",
          },
        },
      ],
    },
  ],
};

const EVENTS: Array<{
  event: string;
  rule: string;
  scale: string;
  notes: React.ReactNode;
}> = [
  {
    event: "Rolex Fastnet Race",
    rule: "IRC",
    scale: "biennial; centenary 2025",
    notes: (
      <>
        RORC + UNCL; 695 nm Cowes → Cherbourg-en-Cotentin. 2025 was the
        centenary edition, started Saturday 26 July.
      </>
    ),
  },
  {
    event: "RORC Caribbean 600",
    rule: "IRC + CSA",
    scale: "annual",
    notes: <>Antigua. 17th edition starts Monday 23 February 2026.</>,
  },
  {
    event: "Rolex Middle Sea Race",
    rule: "IRC + ORC",
    scale: "annual since 1968",
    notes: (
      <>
        606 nm round Sicily. 46th edition started 18 October 2025. Dual-scored
        is the gold-standard cross-rule comparison.
      </>
    ),
  },
  {
    event: "Cowes Week",
    rule: "IRC",
    scale: "annual since 1826",
    notes: <>Cowes Combined Clubs. 2025: 2–8 August.</>,
  },
  {
    event: "Rolex Sydney Hobart",
    rule: "IRC + ORC",
    scale: "annual since 1945",
    notes: <>CYCA, starts 26 December annually.</>,
  },
  {
    event: "Round the Island Race",
    rule: "IRC",
    scale: "the largest IRC entry on earth",
    notes: <>Island Sailing Club, Isle of Wight, since 1931.</>,
  },
  {
    event: "ORC World Championship",
    rule: "ORC",
    scale: "annual",
    notes: (
      <>
        Garmin ORC Worlds, Tallinn, 8–16 August 2025. 2026: Naples / Sorrento,
        5–28 May, hosted by CRVI within Tre Golfi Sailing Week.
      </>
    ),
  },
  {
    event: "ORC European Championship",
    rule: "ORC",
    scale: "annual",
    notes: <>2026: Klaipėda, Lithuania, 7–15 August.</>,
  },
  {
    event: "Newport Bermuda Race",
    rule: "ORR",
    scale: "biennial since 1906",
    notes: (
      <>
        2026: 54th sailing, 100th anniversary of the CCA / RBYC partnership.
        ORC sister-ship inference where applicable.
      </>
    ),
  },
  {
    event: "Giraglia Rolex Cup",
    rule: "ORC",
    scale: "annual since 1953",
    notes: <>YC Italiano + YC de France + Société Nautique de Saint-Tropez.</>,
  },
];

export default function ResultsPage() {
  return (
    <main className="min-h-screen bg-cream">
      <MainNav theme="on-cream" />

      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }}
      />

      <article>
        {/* Masthead */}
        <header className="max-w-4xl mx-auto px-8 sm:px-12 pt-12 sm:pt-20 pb-10">
          <div className="data-mono text-[10px] uppercase tracking-[0.18em] text-brass mb-6">
            Sail Ratings · Reference
          </div>
          <h1
            className="heading-display text-navy"
            style={{ fontSize: "clamp(2.2rem, 5vw, 3.6rem)" }}
          >
            IRC and ORC race results, properly read
          </h1>
          <p
            className="body-text text-charcoal/85 mt-6 max-w-[58ch]"
            style={{ fontSize: "clamp(1.05rem, 1.4vw, 1.2rem)", lineHeight: 1.55 }}
          >
            Every corrected finish your boat has sailed, joined to the
            certificate she carried on the day she sailed it. The truth-test of
            your rating.
          </p>
          <div className="mt-8 mb-2 h-px bg-brass/40" />
          <div className="data-mono text-[10px] uppercase tracking-[0.16em] text-charcoal/45">
            First published May 2026 · 9 min read
          </div>
        </header>

        {/* Lead */}
        <section className="max-w-3xl mx-auto px-8 sm:px-12 pb-2">
          <p className="body-text text-charcoal text-[17px] leading-[1.65] max-w-[62ch]">
            Results without certificates are gossip. Certificates without
            results are theory. This is the page where SailRatings holds both —
            every IRC and ORC race result we can lay hands on, joined
            boat-by-boat to the rating she carried on the day she sailed it.
          </p>
          <p className="body-text text-charcoal text-[17px] leading-[1.65] max-w-[62ch] mt-4 italic">
            That join is the whole point. It is also the part nobody else does.
          </p>
        </section>

        {/* §1 */}
        <section className="max-w-3xl mx-auto px-8 sm:px-12 py-10">
          <h2 className="heading-display text-navy text-2xl sm:text-3xl mb-5">
            What an aggregator of race results is actually for
          </h2>
          <div className="body-text text-charcoal text-[17px] leading-[1.65] max-w-[62ch] space-y-4">
            <p>
              Race-result aggregation, as a category, has a tired reputation.
              PDFs on club websites. Spreadsheets behind logins. Sailwave exports
              posted to a Facebook group on the Tuesday after racing and never
              seen again. The race itself was Saturday; by the time the
              corrected times settle, the bar conversation that needed them has
              moved on.
            </p>
            <p>
              The owner walks away with three questions and no good way to
              answer them.
            </p>
            <ul className="space-y-2 list-none pl-0">
              <li>— Did she sail to her rating?</li>
              <li>
                — Where, in the season, did she actually fall short — and where
                did she sail above it?
              </li>
              <li>
                — What was the boat finishing two places ahead of her{" "}
                <em>carrying</em> on her certificate that day?
              </li>
            </ul>
            <p>
              Those questions are the substance of any honest post-season
              review. They cannot be answered from a single regatta scoreboard.
              They need every result, joined to every certificate, joined to
              every sister-ship, joined to the wind that blew. That is what we
              build.
            </p>
          </div>
        </section>

        <div className="max-w-4xl mx-auto px-8 sm:px-12">
          <div className="h-px bg-brass/30" />
        </div>

        {/* §2 What we hold */}
        <section className="max-w-3xl mx-auto px-8 sm:px-12 py-10">
          <h2 className="heading-display text-navy text-2xl sm:text-3xl mb-5">
            What we hold
          </h2>
          <div className="body-text text-charcoal text-[17px] leading-[1.65] max-w-[62ch] space-y-4">
            <p>
              <strong>Over 31,000 race finishes</strong> stitched into a single
              relational store, growing nightly as the SailSys, RORC, ORC and
              national-authority feeds publish.
            </p>
            <p>
              Each finish bound to a <strong>boat identity</strong> — not a name
              string, an identity. <em>Tonnerre</em> who became{" "}
              <em>Tonnerre de Glen</em> who became <em>Tonnerre 6</em> are one
              boat in our register, with one continuous results timeline. Sail
              numbers change; hulls do not.
            </p>
            <p>
              Each finish bound to the{" "}
              <strong>certificate the boat carried on the day</strong> — IRC TCC
              to four decimal places, or ORC GPH to one, with the underlying
              measurement file behind it.
            </p>
            <p>
              <strong>Corrected times under both rules</strong> where the event
              was dual-scored. The Rolex Middle Sea Race posts IRC and ORC
              results side by side; ORC Worlds cross-reference into IRC for the
              same hulls. We hold all of it.
            </p>
            <p>
              <strong>Fleet context</strong> — class size on the day, the gap to
              the corrected winner, the gap to the boats inside her TCC band.
            </p>
            <p>
              It is, to be frank, the unglamorous part of what SailRatings does.
              The scrapers run at 03:00 and 06:00 and every thirty minutes
              between. The boat-identity matcher reconciles spelling drift,
              country-code changes, ownership transfers. None of that is a
              feature anybody asks for. It is the foundation everything else
              stands on.
            </p>
          </div>
        </section>

        <div className="max-w-4xl mx-auto px-8 sm:px-12">
          <div className="h-px bg-brass/30" />
        </div>

        {/* §3 Corrected time */}
        <section className="max-w-3xl mx-auto px-8 sm:px-12 py-10">
          <h2 className="heading-display text-navy text-2xl sm:text-3xl mb-5">
            Corrected time, properly read
          </h2>
          <div className="body-text text-charcoal text-[17px] leading-[1.65] max-w-[62ch] space-y-4">
            <p>
              A corrected-time line on a results sheet is the most-read and
              least-understood number in offshore sailing. The owner sees the
              gap to the winner and either cheers or curses. The race officer
              signs off and goes home. Almost no one asks the question that
              matters.
            </p>
            <blockquote className="border-l-2 border-brass/50 pl-5 italic text-charcoal/80 my-6">
              Was that gap a function of the boat, the crew, the wind, the
              rating — or all four, in proportions nobody has bothered to
              disentangle?
            </blockquote>
            <p>
              A single corrected result tells you nothing. A season of corrected
              results, joined to the certificate she carried each time and
              benchmarked against every sister and every rival inside her band,
              tells you everything. That is the difference between a results
              website and SailRatings.
            </p>
          </div>
        </section>

        <div className="max-w-4xl mx-auto px-8 sm:px-12">
          <div className="h-px bg-brass/30" />
        </div>

        {/* §4 RAI */}
        <section className="max-w-3xl mx-auto px-8 sm:px-12 py-10">
          <h2 className="heading-display text-navy text-2xl sm:text-3xl mb-5">
            The Racing Advantage Index — what your results actually say
          </h2>
          <div className="body-text text-charcoal text-[17px] leading-[1.65] max-w-[62ch] space-y-4">
            <p>
              The Racing Advantage Index, <strong>RAI</strong>, is the metric
              SailRatings produces when we ask one question of every boat in
              the register: <em>what does her rating predict, and what does she
              actually sail?</em> It is one of the sealed sections in the free
              Bench teaser, and it is the part of the report most owners read
              first.
            </p>
            <p>
              RAI is not a scratch number. It is not a power-ranking. It is the
              residual — the gap between expected finish position (given her
              TCC and the fleet she sailed against) and her observed finish
              position, normalised across hundreds of races and rolled up by
              format: windward-leeward, coastal, offshore, dual-scored under
              both rules. A positive RAI means she beats what the rating
              predicts. A negative RAI means the rating is, on average, kinder
              than her sailing deserves.
            </p>
            <p>
              What it surfaces is uncomfortable in the right way. A boat who
              scores well at <em>Cowes Week</em> against fifteen-strong IRC
              class fleets but bleeds positions at the <em>Round the Island
              Race</em> in 1,400-boat chaos is a boat with a real story to tell
              — about sail wardrobe, about light-air handling, about pinch
              height in tide. RAI does not tell you the cause; it tells you the
              pattern, in numbers, so you can ask the right diagnostic
              question.
            </p>
            <p>
              It works because we hold every finish she has ever sailed under
              both rules, joined to the certificate of the day. Without that
              join, RAI is a guess. With it, it is an audit.
            </p>
          </div>
        </section>

        <div className="max-w-4xl mx-auto px-8 sm:px-12">
          <div className="h-px bg-brass/30" />
        </div>

        {/* §5 Events table */}
        <section className="max-w-4xl mx-auto px-8 sm:px-12 py-10">
          <h2 className="heading-display text-navy text-2xl sm:text-3xl mb-6">
            Events we cover
          </h2>
          <p className="body-text text-charcoal text-[17px] leading-[1.65] max-w-[62ch] mb-6">
            The list grows. Some highlights of what currently sits in the
            register, IRC and ORC and dual-scored.
          </p>
          <div className="overflow-x-auto">
            <table className="w-full text-[14px] border-collapse">
              <thead>
                <tr className="border-b border-brass/40">
                  <th className="text-left py-3 pr-4 data-mono text-[10px] uppercase tracking-[0.16em] text-charcoal/55 font-medium">
                    Event
                  </th>
                  <th className="text-left py-3 px-4 data-mono text-[10px] uppercase tracking-[0.16em] text-charcoal/55 font-medium whitespace-nowrap">
                    Rule
                  </th>
                  <th className="text-left py-3 px-4 data-mono text-[10px] uppercase tracking-[0.16em] text-charcoal/55 font-medium whitespace-nowrap">
                    Scale
                  </th>
                  <th className="text-left py-3 px-4 data-mono text-[10px] uppercase tracking-[0.16em] text-charcoal/55 font-medium">
                    Notes
                  </th>
                </tr>
              </thead>
              <tbody className="body-text text-charcoal">
                {EVENTS.map((row) => (
                  <tr key={row.event} className="border-b border-border-light/60">
                    <td className="py-3 pr-4 align-top heading-display text-navy text-[14px] font-semibold w-[22%]">
                      {row.event}
                    </td>
                    <td className="py-3 px-4 align-top data-mono text-[12px] text-charcoal/80 whitespace-nowrap">
                      {row.rule}
                    </td>
                    <td className="py-3 px-4 align-top text-[13px] text-charcoal/70">
                      {row.scale}
                    </td>
                    <td className="py-3 px-4 align-top text-[14px]">
                      {row.notes}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="body-text text-charcoal text-[16px] leading-[1.6] max-w-[62ch] mt-6">
            This is not a manifest of every event. It is a sample of the spine.
            Smaller national series, club-level championships and the long tail
            of regional offshore — Cap Martinique, Palermo–Montecarlo, the 151
            Miglia, the Fastnet feeder races, RORC Season&rsquo;s Points — sit
            underneath, joined into the same boat identities and the same
            certificates.
          </p>
          <p className="body-text text-charcoal text-[16px] leading-[1.6] max-w-[62ch] mt-3 italic">
            If your boat sailed it and the results were published, we are
            probably looking at her finish.
          </p>
        </section>

        <div className="max-w-4xl mx-auto px-8 sm:px-12">
          <div className="h-px bg-brass/30" />
        </div>

        {/* §6 Why both */}
        <section className="max-w-3xl mx-auto px-8 sm:px-12 py-10">
          <h2 className="heading-display text-navy text-2xl sm:text-3xl mb-5">
            Why it matters that both rules sit side by side
          </h2>
          <div className="body-text text-charcoal text-[17px] leading-[1.65] max-w-[62ch] space-y-4">
            <p>
              A boat scored under IRC and a boat scored under ORC at the same
              event are not really racing each other; they are racing two
              different abstractions of themselves. IRC&rsquo;s secret formula
              returns a single TCC and applies it to elapsed time. ORC&rsquo;s
              open Velocity Prediction Program returns a polar across a wind
              matrix and lets the race committee pick from Time-on-Distance,
              Time-on-Time, Triple Number, Performance Curve Scoring or
              Weather Routing Scoring.
            </p>
            <p>
              The implication for a results database is non-trivial. A{" "}
              <em>Swan 50</em> who corrected fifth on IRC at the Rolex Middle
              Sea Race and ninth on ORC at the same race is not contradicting
              herself. She is telling you that the IRC formula and the ORC VPP
              weight her sailing characteristics differently in the wind track
              she actually saw — and that is a piece of strategic intelligence
              worth having. We surface it because we hold both rules&rsquo;
              corrected times against the same finish line crossing.
            </p>
            <p>
              For the longer explanation of how the two rules differ — what GPH
              actually represents, why ORC&rsquo;s polar is published and
              IRC&rsquo;s formula is not — see{" "}
              <a href="/ratings" className="text-navy underline decoration-brass/60 underline-offset-4 hover:decoration-brass">
                our scoring-system primer
              </a>
              .
            </p>
          </div>
        </section>

        {/* CTA */}
        <FunnelCTA
          headline={
            <>
              Pull a boat. The Bench drafts her file in about four seconds:
              where she sits today, where her TCC has drifted, and where her
              RAI sits against her actual finishes.
            </>
          }
          subline="Free to open. One certificate, one report."
          buttonLabel="Open the Bench"
        />

        {/* FAQ */}
        <section className="max-w-3xl mx-auto px-8 sm:px-12 py-12 sm:py-16">
          <h2 className="data-mono text-[11px] uppercase tracking-[0.18em] text-brass mb-8">
            Frequently asked
          </h2>
          <div className="space-y-8 max-w-[62ch]">
            {[
              {
                q: "Where do the results come from?",
                a: "Public scoring outputs from RORC, ORC, SailSys-hosted club series, national authorities and event organising authorities. We do not republish protected data, we do not scrape behind logins, and we credit source organisations on every event page.",
              },
              {
                q: "How current is the data?",
                a: "SailSys results refresh every thirty minutes. ORC certificate scrape runs nightly at 03:00 UTC. IRC certificate discovery and parsing runs weekly. Major-event corrected times typically appear within hours of the OA posting them.",
              },
              {
                q: "Can I correct an error in my boat's record?",
                a: "Yes. The pencil-icon next to any masthead field on the Bench submits a correction into our moderation queue. We review and merge daily.",
              },
            ].map(({ q, a }) => (
              <div key={q}>
                <h3 className="heading-display text-navy text-[18px] sm:text-[20px] mb-2">
                  {q}
                </h3>
                <p className="body-text text-charcoal text-[16px] leading-[1.6]">
                  {a}
                </p>
              </div>
            ))}
          </div>
        </section>
      </article>

      <EditorialFooter />
    </main>
  );
}

import type { Metadata } from "next";
import MainNav from "@/components/MainNav";
import FunnelCTA from "@/components/FunnelCTA";
import EditorialFooter from "@/components/EditorialFooter";

export const metadata: Metadata = {
  title: "Yacht fleet rating analysis",
  description:
    "See how your boat sits against every sister on the IRC and ORC registers — and where her design rates against the boats sharing her start line.",
  alternates: { canonical: "/fleet" },
  openGraph: {
    title: "Where she sits among her sisters",
    description:
      "Every certificate, every sister-ship, one comparison. IRC and ORC fleet analysis for owners, race officers and committees.",
    url: "https://sailratings.com/fleet",
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
        { "@type": "ListItem", position: 2, name: "Fleet analysis", item: "https://sailratings.com/fleet" },
      ],
    },
    {
      "@type": "Article",
      headline: "Yacht fleet rating analysis — where she sits among her sisters",
      description:
        "See how your boat sits against every sister on the IRC and ORC registers — and where her design rates against the boats sharing her start line.",
      author: { "@type": "Organization", name: "Sail Ratings" },
      publisher: {
        "@type": "Organization",
        name: "Sail Ratings",
        url: "https://sailratings.com",
      },
      mainEntityOfPage: "https://sailratings.com/fleet",
    },
    {
      "@type": "FAQPage",
      mainEntity: [
        {
          "@type": "Question",
          name: "How is a fleet rating analysis different from just looking at the register?",
          acceptedAnswer: {
            "@type": "Answer",
            text: "The register gives you a list of certificates. A fleet analysis ranks the variance across that list, attributes the differences to specific declared inputs — sail count, engine and propeller, crew number, weight — and overlays empirical race results so you can see what the rating predicts versus what actually happens on the water.",
          },
        },
        {
          "@type": "Question",
          name: "Does fleet rating analysis work for ORC as well as IRC?",
          acceptedAnswer: {
            "@type": "Answer",
            text: "Yes. The comparison is technically cleaner under ORC because the Velocity Prediction Program polar is published at every wind speed, but the underlying job is the same. Sail Ratings holds both the IRC and ORC registers.",
          },
        },
        {
          "@type": "Question",
          name: "Can I compare my boat against different designs in the same rating band?",
          acceptedAnswer: {
            "@type": "Answer",
            text: "Yes. The cross-design band view shows every other design within a defined TCC or GPH window of your boat — the boats you actually share a start line with — with a one-line read on each.",
          },
        },
        {
          "@type": "Question",
          name: "Is Sail Ratings affiliated with the RORC Rating Office or ORC?",
          acceptedAnswer: {
            "@type": "Answer",
            text: "No. The certificate data is public. Sail Ratings holds it, synthesises it, and presents it for comparison. The rating offices issue certificates; we do the comparative work on top.",
          },
        },
        {
          "@type": "Question",
          name: "What does a fleet analysis report cost?",
          acceptedAnswer: {
            "@type": "Answer",
            text: "The opening section — where your boat sits today — is free. The full file, including the sister-ship and rating-band views, is a one-time purchase per boat, delivered as a PDF the moment payment clears.",
          },
        },
      ],
    },
  ],
};

export default function FleetPage() {
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
            Where she sits among her sisters
          </h1>
          <p
            className="body-text text-charcoal/85 mt-6 max-w-[58ch]"
            style={{ fontSize: "clamp(1.05rem, 1.4vw, 1.2rem)", lineHeight: 1.55 }}
          >
            Every certificate of every sister, side by side. Yacht fleet rating
            analysis for owners, race officers and class committees.
          </p>
          <div className="mt-8 mb-2 h-px bg-brass/40" />
          <div className="data-mono text-[10px] uppercase tracking-[0.16em] text-charcoal/45">
            First published May 2026 · 8 min read
          </div>
        </header>

        {/* Lead */}
        <section className="max-w-3xl mx-auto px-8 sm:px-12 pb-2">
          <p className="body-text text-charcoal text-[17px] leading-[1.65] max-w-[62ch]">
            The question gets asked in every dinghy park after racing.
            &ldquo;Why does the J/109 on D pontoon rate three thousandths lower
            than mine? Same boat, same year, same sails — what&rsquo;s she
            declared that I haven&rsquo;t?&rdquo; Yacht fleet rating analysis is
            the work of answering that, properly: pulling every certificate of
            every sister on the register, laying them side by side, and reading
            where the differences sit. Not a rumour at the bar. The actual
            numbers.
          </p>
          <p className="body-text text-charcoal text-[17px] leading-[1.65] max-w-[62ch] mt-4">
            It is also the work nobody does for you. The Royal Ocean Racing Club
            Rating Office issues your certificate. The Offshore Racing Congress
            publishes your polar. Neither hands you the comparison. We do.
          </p>
        </section>

        {/* §1 What "fleet" means */}
        <section className="max-w-3xl mx-auto px-8 sm:px-12 py-10">
          <h2 className="heading-display text-navy text-2xl sm:text-3xl mb-5">
            What &ldquo;fleet&rdquo; means here
          </h2>
          <div className="body-text text-charcoal text-[17px] leading-[1.65] max-w-[62ch] space-y-4">
            <p>Two readings of the word, and we run both.</p>
            <p>
              <strong>Inside your design class.</strong> Every <em>J/109</em> on
              the IRC register. Every <em>Sun Fast 3300</em> on the ORC list.
              Every <em>TP52</em> with a current certificate, anywhere in the
              world. Same hull, same rig geometry on paper — and yet rarely the
              same TCC, almost never the same GPH. The variance is the story.
              Owner declarations on engine and propeller, sail wardrobe count,
              crew-number limits, weight after the last refit, optional gear
              listed or removed — every one of them moves the number. The fleet
              view shows you which lever each of your sisters has pulled.
            </p>
            <p>
              <strong>Across the rating band you actually start with.</strong> A{" "}
              <em>Sun Fast 3300</em> rating around 1.0070 doesn&rsquo;t only race
              other 3300s. She lines up against <em>J/99</em>s, <em>First 36</em>
              s, JPK 1030s, the lighter Italia 9.98s — anything sitting within
              ±0.010 TCC of her. Cross-design comparison is the harder question,
              because the boats are no longer the same shape. But it&rsquo;s the
              question that actually decides your weekend. We hold the data on
              every design in the band; we plot where each one rates light and
              where each rates heavy.
            </p>
            <p>
              If you want the mechanics of how a TCC or a GPH gets to the number
              it does, that sits on our{" "}
              <a href="/ratings" className="text-navy underline decoration-brass/60 underline-offset-4 hover:decoration-brass">
                ratings page
              </a>
              . This page is about what to do with those numbers once you have
              them.
            </p>
          </div>
        </section>

        <div className="max-w-4xl mx-auto px-8 sm:px-12">
          <div className="h-px bg-brass/30" />
        </div>

        {/* §2 Owner's question */}
        <section className="max-w-3xl mx-auto px-8 sm:px-12 py-10">
          <h2 className="heading-display text-navy text-2xl sm:text-3xl mb-5">
            The owner&rsquo;s question, answered with data
          </h2>
          <div className="body-text text-charcoal text-[17px] leading-[1.65] max-w-[62ch] space-y-4">
            <p>
              Take the active <em>J/109</em> fleet on the global IRC register —
              seventy boats with current certificates in our store. They are, at
              the level of the production drawing, identical boats. Frers-designed,
              built by J/Boats from 2002 onwards, fixed lead keel, fractional
              rig, masthead asymmetric. The class TCC range across that fleet
              runs from roughly 1.001 to 1.022 inter-quartile, with outliers
              either side. About two percent of corrected time between the
              lightest-rating and heaviest-rating boat of the same model.
            </p>
            <p>
              Where does that variance come from? In aggregate, on the J/109
              register, we see it cluster around four declared inputs:
            </p>
            <ul className="space-y-2 list-none pl-0">
              <li>
                <strong>Headsail inventory.</strong> A cruising-cut #3 declared
                and used as the rating reducer; a single all-purpose genoa
                versus a wardrobe of three.
              </li>
              <li>
                <strong>Spinnaker count.</strong> Two asymmetrics versus four.
                The fourth boat&rsquo;s TCC moves; the difference is real.
              </li>
              <li>
                <strong>Engine and propeller.</strong> Folding two-blade declared
                honestly versus the boats that haven&rsquo;t updated their
                certificate since they swapped to a feathering three-blade.
              </li>
              <li>
                <strong>Crew number limit.</strong> Eight on the card or six.
                The lower number rates the boat lighter.
              </li>
            </ul>
            <p>
              A fleet view makes those clusters visible. You don&rsquo;t have to
              ask each rival what&rsquo;s on his certificate — the data is on
              the published register and we have it.
            </p>
          </div>
        </section>

        <div className="max-w-4xl mx-auto px-8 sm:px-12">
          <div className="h-px bg-brass/30" />
        </div>

        {/* §3 Cross-design */}
        <section className="max-w-3xl mx-auto px-8 sm:px-12 py-10">
          <h2 className="heading-display text-navy text-2xl sm:text-3xl mb-5">
            The cross-design view: who&rsquo;s in your band
          </h2>
          <div className="body-text text-charcoal text-[17px] leading-[1.65] max-w-[62ch] space-y-4">
            <p>
              This is the part that is genuinely hard to get anywhere else. Your
              TCC is a single number. The boat sitting two places ahead of you
              on the start-line scratch sheet has a different hull, a different
              rig, a different displacement — and a TCC within five thousandths
              of yours. Whose rating is the more generous?
            </p>
            <p>
              The honest answer is: it depends on the day. Under IRC the formula
              is undisclosed by design — the Royal Ocean Racing Club has held
              that line for thirty years and the rule&rsquo;s competitive longevity
              is the reward. Under ORC the polar is published, which means
              cross-design comparison can be done at every wind speed from 4 to
              24 knots. We hold both. For an IRC-only owner we show you where
              the empirical results have fallen across the band; for an ORC
              owner we show you the polars side by side and where the deltas
              live.
            </p>
            <p>
              A worked example. A <em>Sun Fast 3300</em> around 1.0070 typically
              gives time to a <em>J/99</em> around 1.0210 in light airs and
              takes time back when the breeze fills in past 16 knots. That&rsquo;s
              the kind of pattern the single-number rating obscures and the
              polar reveals. Most owners feel it on the water — they just
              can&rsquo;t see it written down. The fleet view writes it down.
            </p>
          </div>
        </section>

        <div className="max-w-4xl mx-auto px-8 sm:px-12">
          <div className="h-px bg-brass/30" />
        </div>

        {/* §4 What the report shows */}
        <section className="max-w-3xl mx-auto px-8 sm:px-12 py-10">
          <h2 className="heading-display text-navy text-2xl sm:text-3xl mb-5">
            What the report actually shows
          </h2>
          <div className="body-text text-charcoal text-[17px] leading-[1.65] max-w-[62ch] space-y-4">
            <p>
              Pull a boat into the Bench and the fleet section of her report
              does three jobs.
            </p>
            <p>
              <strong>Sister-ship column.</strong> Every active certificate of
              her design, by country, with TCC or GPH, certificate date,
              headsail count, spinnaker count, engine/prop declaration,
              crew-number limit. Where she rates light against the class mean,
              where she rates heavy. The lines are tagged: this declaration
              moves her down two thousandths; this one moves her up four.
            </p>
            <p>
              <strong>Band view.</strong> Every other design within ±0.010 TCC
              under IRC, or within a defined GPH window under ORC, with a
              one-line note on each — what kind of boat she is, what wind she
              likes, where her edge sits. This is the scratch-sheet you wish
              the race office handed you in the briefing.
            </p>
            <p>
              <strong>Empirical overlay.</strong> Where the fleet has actually
              finished. Not the rule&rsquo;s prediction — the results from the
              past three seasons of races that scored that band. A <em>TP52</em>{" "}
              might rate generously on paper and habitually clean up in the
              Mediterranean offshore circuit; her sisters&rsquo; results say so.
              The empirical layer is what separates fleet analysis from fleet{" "}
              <em>speculation</em>. More on that read in{" "}
              <a href="/results" className="text-navy underline decoration-brass/60 underline-offset-4 hover:decoration-brass">
                results
              </a>
              .
            </p>
          </div>
        </section>

        <div className="max-w-4xl mx-auto px-8 sm:px-12">
          <div className="h-px bg-brass/30" />
        </div>

        {/* §5 Comparison table */}
        <section className="max-w-4xl mx-auto px-8 sm:px-12 py-10">
          <h2 className="heading-display text-navy text-2xl sm:text-3xl mb-6">
            Two designs, side by side
          </h2>
          <div className="overflow-x-auto">
            <table className="w-full text-[14px] border-collapse">
              <thead>
                <tr className="border-b border-brass/40">
                  <th className="text-left py-3 pr-4 data-mono text-[10px] uppercase tracking-[0.16em] text-charcoal/55 font-medium"></th>
                  <th className="text-left py-3 px-4 heading-display text-navy text-[15px] font-semibold italic">
                    J/109
                  </th>
                  <th className="text-left py-3 px-4 heading-display text-navy text-[15px] font-semibold italic">
                    First 36
                  </th>
                </tr>
              </thead>
              <tbody className="body-text text-charcoal">
                {[
                  ["Designer", "Frers", "Sam Manuard"],
                  ["Builder", "J/Boats", "Beneteau"],
                  ["LH", "10.66 m", "10.85 m"],
                  ["Displacement", "~5,500 kg", "~5,200 kg"],
                  ["Typical IRC TCC", "~1.001–1.022", "~1.000–1.020"],
                  ["Light-air strength", "Moderate", "Strong"],
                  ["Heavy-air strength", "Strong", "Moderate"],
                  ["Owner-driver bias", "Yes", "Yes"],
                ].map(([row, a, b]) => (
                  <tr key={row} className="border-b border-border-light/60">
                    <td className="py-3 pr-4 align-top text-charcoal/70 font-medium text-[13px] w-[28%]">
                      {row}
                    </td>
                    <td className="py-3 px-4 align-top">{a}</td>
                    <td className="py-3 px-4 align-top">{b}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="body-text text-charcoal text-[16px] leading-[1.6] max-w-[62ch] mt-6">
            The numbers are similar. The shapes are not. The <em>J/109</em> is
            the older, narrower, heavier boat — she earns her TCC by being kind
            to crew and forgiving in breeze. The <em>First 36</em> is wider,
            lighter, with more form stability — she earns hers in the lighter
            end of the wind range. Same start line, often the same first beat,
            very different read on what wind they wanted. A fleet view tells you
            which one to expect to see ahead at the windward mark on a 9-knot
            day.
          </p>
        </section>

        <div className="max-w-4xl mx-auto px-8 sm:px-12">
          <div className="h-px bg-brass/30" />
        </div>

        {/* §6 Who uses this */}
        <section className="max-w-3xl mx-auto px-8 sm:px-12 py-10">
          <h2 className="heading-display text-navy text-2xl sm:text-3xl mb-5">
            Who actually uses this
          </h2>
          <div className="body-text text-charcoal text-[17px] leading-[1.65] max-w-[62ch] space-y-4">
            <p>Three readers, three different uses.</p>
            <p>
              <strong>Owners.</strong> Mostly to settle an argument with
              themselves. The boat felt slow on Saturday. Was it the boat, was
              it the trim, was it the rating, was it the rivals? The fleet view
              doesn&rsquo;t blame anyone — it shows you what the certificate
              register says and what the results from the same fleet say. Often
              that&rsquo;s enough to tell a tactician what to work on next.
            </p>
            <p>
              <strong>Club race officers and handicap committees.</strong> Most
              clubs run a mixed IRC fleet and a couple of ORC entries; the
              handful with ambition run their own scratch handicap committee on
              top. Those committees need cross-design comparison to make
              defensible decisions when they split classes, weight series, or
              handle a borderline appeal. We don&rsquo;t replace the rating
              offices — we sit alongside, with the open data, and show our
              working.
            </p>
            <p>
              <strong>Sailmakers, designers, measurers.</strong> The technical
              fringe reads the sister-ship view to track where the
              production-cruiser-racer fleet is converging. When seventy{" "}
              <em>J/109</em>s show the same headsail count cluster, that is a
              market signal. When a class drifts on average TCC across a rule
              revision, that is a technical signal. The data is public; the
              synthesis isn&rsquo;t, generally.
            </p>
          </div>
        </section>

        {/* CTA */}
        <FunnelCTA
          headline={
            <>
              Pull a boat. Yours, your rival&rsquo;s, the boat in front of you
              in last weekend&rsquo;s results. The Bench shows where she sits
              among her sisters.
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
                q: "How is fleet analysis different from the public register?",
                a: "The register gives you a list of certificates. A fleet analysis ranks the variance, attributes it to specific declared inputs — sail count, engine and prop, crew number, weight — and overlays empirical race results so you can see what the rating predicts versus what actually happens.",
              },
              {
                q: "Does this work for ORC as well as IRC?",
                a: "Yes — the comparison is cleaner under ORC because the polar is published, but the underlying job is the same. We hold both registers.",
              },
              {
                q: "Can I compare across designs, not just within my class?",
                a: "Yes. The cross-design band view is the harder question and the more valuable one for race-day tactical reading.",
              },
              {
                q: "Are you affiliated with the RORC Rating Office or ORC?",
                a: "No. The certificate data is public. We hold it, we synthesise it, we don't issue it.",
              },
              {
                q: "What does it cost?",
                a: "A free teaser opens with where your boat sits today; the full file — including sister-ship and band views — is a one-time purchase per boat.",
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

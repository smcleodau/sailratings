import type { Metadata } from "next";
import MainNav from "@/components/MainNav";
import FunnelCTA from "@/components/FunnelCTA";
import EditorialFooter from "@/components/EditorialFooter";

export const metadata: Metadata = {
  title: "IRC vs ORC: two rules, one boat",
  description:
    "How IRC and ORC actually differ — secret formula vs open VPP, TCC vs GPH, what each rule rewards, where each lives, and what your boat would do under the other.",
  alternates: { canonical: "/ratings" },
  openGraph: {
    title: "IRC vs ORC — two rules, two philosophies, one boat",
    description:
      "A working comparison from people who read both certificates every day. What each rule rewards, where each dominates, and what your TCC would look like as a GPH.",
    url: "https://sailratings.com/ratings",
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
        { "@type": "ListItem", position: 2, name: "Ratings", item: "https://sailratings.com/ratings" },
      ],
    },
    {
      "@type": "Article",
      headline: "IRC vs ORC: two rules, two philosophies, one boat",
      description:
        "How IRC and ORC actually differ — secret formula vs open VPP, TCC vs GPH, what each rule rewards, where each lives, and what your boat would do under the other.",
      author: { "@type": "Organization", name: "Sail Ratings" },
      publisher: {
        "@type": "Organization",
        name: "Sail Ratings",
        url: "https://sailratings.com",
      },
      mainEntityOfPage: "https://sailratings.com/ratings",
    },
    {
      "@type": "FAQPage",
      mainEntity: [
        {
          "@type": "Question",
          name: "Is ORC fairer than IRC?",
          acceptedAnswer: {
            "@type": "Answer",
            text: "Neither rule is fairer — they are optimised for different things. ORC's polar-based scoring is more precise in known wind conditions; Performance Curve Scoring in particular is the gold standard for fairness when the race committee has good wind data. IRC is more durable across conditions and across boat ages because no one can game what they cannot see.",
          },
        },
        {
          "@type": "Question",
          name: "Can I race the Rolex Fastnet Race on an ORC certificate?",
          acceptedAnswer: {
            "@type": "Answer",
            text: "No. The Rolex Fastnet Race scores under IRC; ORC certificates are not accepted. Two-Handed competes as a sub-class within IRC.",
          },
        },
        {
          "@type": "Question",
          name: "What is the difference between TCC and GPH?",
          acceptedAnswer: {
            "@type": "Answer",
            text: "TCC (Time Correction Coefficient) is IRC's single output — a multiplier applied to elapsed time. GPH (General Purpose Handicap) is ORC's most-cited single number, expressed in seconds per nautical mile, derived from the VPP's predicted times around a windward-leeward course in 8–12 knots. They cannot be converted between each other.",
          },
        },
        {
          "@type": "Question",
          name: "My boat rates well in IRC. Will she rate well in ORC?",
          acceptedAnswer: {
            "@type": "Answer",
            text: "Probably not in the same way. IRC tends to reward stiff, narrow boats with moderate sail area; ORC reads the full hull and rig and credits or penalises specific shapes via the Velocity Prediction Program. Light, beamy modern designs with twin rudders often look better under ORC than under IRC. Older masthead cruiser-racers often look better under IRC.",
          },
        },
        {
          "@type": "Question",
          name: "Do I need to be re-measured to switch between IRC and ORC?",
          acceptedAnswer: {
            "@type": "Answer",
            text: "To switch from IRC Standard to IRC Endorsed, yes — an authorised measurer must verify hull, rig, and sails. To obtain an ORC certificate, yes — measurement, an inclining test, and a hull file are required, regardless of any IRC papers already held. The two rules do not share data.",
          },
        },
      ],
    },
  ],
};

export default function RatingsPage() {
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
            IRC vs ORC: two rules, two philosophies, one boat
          </h1>
          <p
            className="body-text text-charcoal/85 mt-6 max-w-[58ch]"
            style={{ fontSize: "clamp(1.05rem, 1.4vw, 1.2rem)", lineHeight: 1.55 }}
          >
            Most owners pick a handicap rule the same way they pick a sailmaker —
            by who their club uses. That works until the calendar widens.
          </p>
          <div className="mt-8 mb-2 h-px bg-brass/40" />
          <div className="data-mono text-[10px] uppercase tracking-[0.16em] text-charcoal/45">
            First published May 2026 · 8 min read
          </div>
        </header>

        {/* Lead */}
        <section className="max-w-3xl mx-auto px-8 sm:px-12 pb-2">
          <p className="body-text text-charcoal text-[17px] leading-[1.65] max-w-[62ch]">
            The moment a Solent boat enters the Rolex Middle Sea Race, or a Grand
            Soleil 44 from Porto Cervo gets an invitation to the RORC Caribbean
            600, the <em>irc vs orc</em> question stops being academic. Two rules,
            two philosophies, one boat — and the answer to &ldquo;where will she
            rate&rdquo; depends on which office issues the certificate.
          </p>
        </section>

        {/* §1 The split */}
        <section className="max-w-3xl mx-auto px-8 sm:px-12 py-10">
          <h2 className="flex items-baseline gap-3 mb-5">
            <span className="data-mono text-brass text-[11px] uppercase tracking-[0.18em]">§1</span>
            <span className="heading-display text-navy text-2xl sm:text-3xl">
              The split, in one paragraph
            </span>
          </h2>
          <div className="body-text text-charcoal text-[17px] leading-[1.65] max-w-[62ch] space-y-4">
            <p>
              The Royal Ocean Racing Club&rsquo;s IRC rule is a <strong>secret formula</strong>.
              You declare your measurements, the Rating Office returns a single
              coefficient — <strong>TCC</strong>, the Time Correction Coefficient —
              and the working algebra is locked in a vault in Lymington. The
              Offshore Racing Congress does the opposite. Its rule is a{" "}
              <strong>published Velocity Prediction Program</strong>, descended
              from the H. Irving Pratt Project at MIT, which models your boat&rsquo;s
              speed at every wind angle and wind speed and reports a polar table.
              The single number it surfaces — <strong>GPH</strong>, the General
              Purpose Handicap — is just one slice of that polar.
            </p>
            <p>
              IRC&rsquo;s secrecy is its shield: in three decades it has never had a
              loophole boat the way IOR did with the bumps and IMS did with the
              chines. ORC&rsquo;s transparency is its precision: a navigator with the
              certificate can tell you, before the start, which leg gives her
              boat back time and which leg costs it.
            </p>
          </div>
        </section>

        <div className="max-w-4xl mx-auto px-8 sm:px-12">
          <div className="h-px bg-brass/30" />
        </div>

        {/* §2 Side by side table */}
        <section className="max-w-4xl mx-auto px-8 sm:px-12 py-10">
          <h2 className="flex items-baseline gap-3 mb-6">
            <span className="data-mono text-brass text-[11px] uppercase tracking-[0.18em]">§2</span>
            <span className="heading-display text-navy text-2xl sm:text-3xl">
              Side by side
            </span>
          </h2>

          <div className="overflow-x-auto">
            <table className="w-full text-[14px] border-collapse">
              <thead>
                <tr className="border-b border-brass/40">
                  <th className="text-left py-3 pr-4 data-mono text-[10px] uppercase tracking-[0.16em] text-charcoal/55 font-medium"></th>
                  <th className="text-left py-3 px-4 data-mono text-[11px] uppercase tracking-[0.16em] text-navy font-semibold">
                    IRC
                  </th>
                  <th className="text-left py-3 px-4 data-mono text-[11px] uppercase tracking-[0.16em] text-navy font-semibold">
                    ORC
                  </th>
                </tr>
              </thead>
              <tbody className="body-text text-charcoal">
                {[
                  ["Owner", "Royal Ocean Racing Club + UNCL", "Offshore Racing Congress"],
                  ["Output", "Single coefficient: TCC", "Full polar; surface numbers GPH and APH"],
                  ["Formula", "Closed, unpublished", "Open, published VPP"],
                  ["Scoring", "Time-on-Time only", "Time-on-Distance, Time-on-Time, Triple Number, Performance Curve Scoring, Weather Routing Scoring"],
                  ["Stability data", "Declared / inferred", "Inclining test, full GZ curve"],
                  ["Hull data", "Declared dimensions", "Full IMS hull file from offsets"],
                  ["Self-declaration", "Yes (IRC Standard)", "No — measurement required"],
                  ["Measured tier", "IRC Endorsed", "ORC International"],
                  ["Light tier for clubs", "n/a", "ORC Club"],
                  ["Renewal", "Annual; UK 1 Jun–31 May, most jurisdictions calendar year", "Annual, calendar year"],
                  ["Owner portal", "MyIRC at rorcrating.com", "ORC Sailor Services at data.orc.org"],
                  ["Wind range modelled", "Implicit in the coefficient", "4–24 kt true (range extended in 2025)"],
                ].map(([row, irc, orc]) => (
                  <tr key={row} className="border-b border-border-light/60">
                    <td className="py-3 pr-4 align-top text-charcoal/70 font-medium text-[13px] w-[28%]">
                      {row}
                    </td>
                    <td className="py-3 px-4 align-top">{irc}</td>
                    <td className="py-3 px-4 align-top">{orc}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <p className="body-text text-charcoal/70 text-[14px] mt-6 max-w-[62ch] italic">
            The right-hand column is longer because it has to be. ORC carries
            more rule surface area precisely because it tells you everything. IRC
            carries less because it tells you nothing — you get the number, you
            go racing.
          </p>
        </section>

        <div className="max-w-4xl mx-auto px-8 sm:px-12">
          <div className="h-px bg-brass/30" />
        </div>

        {/* §3 What each rule rewards */}
        <section className="max-w-3xl mx-auto px-8 sm:px-12 py-10">
          <h2 className="flex items-baseline gap-3 mb-5">
            <span className="data-mono text-brass text-[11px] uppercase tracking-[0.18em]">§3</span>
            <span className="heading-display text-navy text-2xl sm:text-3xl">
              What each rule rewards in practice
            </span>
          </h2>
          <p className="body-text text-charcoal text-[17px] leading-[1.65] max-w-[62ch] mb-8">
            Three boats most owners can picture without prompting. Each one shows
            where the philosophical split bites.
          </p>

          <div className="space-y-10">
            {/* Sun Fast 3300 */}
            <div>
              <h3 className="heading-display text-navy text-xl sm:text-[22px] mb-2">
                <em>Sun Fast 3300</em> — short-handed offshore
              </h3>
              <div className="data-mono text-[10px] uppercase tracking-[0.14em] text-charcoal/50 mb-4">
                IRC TCC <span className="text-charcoal">~1.007</span>{" "}
                <span className="text-charcoal/40">·</span> n=250 boats on the register
              </div>
              <div className="body-text text-charcoal text-[17px] leading-[1.65] max-w-[62ch] space-y-3">
                <p>
                  The Andrieu/Verdier-designed, Jeanneau-built <em>Sun Fast 3300</em>{" "}
                  is the most universally recognised short-handed offshore racer
                  afloat right now. Light displacement, hard chine aft, twin
                  rudders, masthead asymmetric — every feature aimed at downwind
                  VMG with two people on board.
                </p>
                <p>
                  Under IRC her TCC sits in a tight inter-quartile band of{" "}
                  <strong>0.990 to 1.020</strong>. The rule&rsquo;s opacity actually
                  helps her: the secret formula bakes in a moderate downwind
                  credit for twin-ruddered light boats, but does not let a
                  designer optimise <em>to</em> the credit. So 3300s rate close
                  together regardless of how the owner has fettled the boat.
                </p>
                <p>
                  Under ORC the same hull is read more literally. The VPP sees
                  the chine, the rudders, the displacement-to-length, and
                  produces a polar that gives back genuine time downwind in 12–18
                  knots — and gives a chunk of it back upwind in a chop. A 3300
                  owner who races IRC inshore and ORC offshore will see her boat
                  rate &ldquo;differently&rdquo; between the two. She is not. The
                  rule is reading her differently.
                </p>
              </div>
            </div>

            {/* J/109 */}
            <div>
              <h3 className="heading-display text-navy text-xl sm:text-[22px] mb-2">
                <em>J/109</em> — inshore cruiser-racer
              </h3>
              <div className="data-mono text-[10px] uppercase tracking-[0.14em] text-charcoal/50 mb-4">
                IRC TCC <span className="text-charcoal">~1.004</span>{" "}
                <span className="text-charcoal/40">·</span> ORC GPH{" "}
                <span className="text-charcoal">~615 s/nm</span>{" "}
                <span className="text-charcoal/40">·</span> n=70 IRC, n=8 ORC
              </div>
              <div className="body-text text-charcoal text-[17px] leading-[1.65] max-w-[62ch] space-y-3">
                <p>
                  The <em>J/109</em> is the everyman benchmark — a J/Boats-designed,
                  deck-stepped masthead 35-footer with massive class fleets in
                  the UK, US, Caribbean, and Australia. Moderate displacement,
                  conservative sail area, no fancy tricks. If a question begins
                  &ldquo;what does the rule do to my points if&hellip;&rdquo;, a
                  J/109 is usually the boat in the question.
                </p>
                <p>
                  Under IRC her TCC sits in the <strong>1.001 to 1.022</strong>{" "}
                  inter-quartile band depending on sail wardrobe and prop
                  installation, and the optimisation conversation is small and
                  well-trodden. Drop a spinnaker, fix the prop declaration, weigh
                  her honestly. Three thousandths back, perhaps four.
                </p>
                <p>
                  Under ORC the same boat surfaces as a GPH around the{" "}
                  <strong>610 to 620 s/nm</strong> mark on a smaller sample of
                  boats — and, crucially, the rule will tell the owner exactly
                  why. The polar shows the J/109 rating slightly heavy upwind and
                  slightly light downwind versus a Grand Soleil 39 of comparable
                  displacement. An IRC owner who has never read a polar finds
                  this revelatory. She is the same boat. The rule is just more
                  articulate about her.
                </p>
              </div>
            </div>

            {/* TP52 */}
            <div>
              <h3 className="heading-display text-navy text-xl sm:text-[22px] mb-2">
                <em>TP52</em> — Grand Prix inshore
              </h3>
              <div className="data-mono text-[10px] uppercase tracking-[0.14em] text-charcoal/50 mb-4">
                IRC TCC <span className="text-charcoal">~1.363</span>{" "}
                <span className="text-charcoal/40">·</span> n=36 boats on the register
              </div>
              <div className="body-text text-charcoal text-[17px] leading-[1.65] max-w-[62ch] space-y-3">
                <p>
                  At the powered-up end the gap opens widest. The <em>TP52</em>{" "}
                  — Botín, Reichel/Pugh, Judel/Vrolijk, take your pick — is an
                  all-carbon, pro-crewed, jib-top 52-footer designed inside a
                  one-design box rule. Around 7,300 kg, a full inventory, twin
                  wheels, a deck-spreader rig and stacked-out crew.
                </p>
                <p>
                  Under IRC the fleet rates in the <strong>1.351 to 1.392</strong>{" "}
                  inter-quartile band. The opacity matters here in a way it does
                  not at the J/109 end. Designers cannot reverse-engineer rating
                  credit because they cannot see the formula. So TP52 design has
                  stabilised — the boats look the same because optimisation
                  against IRC has nowhere left to go.
                </p>
                <p>
                  Under ORC the same hulls are read by the VPP in full. Polars
                  get exchanged between programmes the way photos used to. The
                  technical fringe of the sport — sailmakers, navigators,
                  designers — lives on the ORC side at this end of the fleet
                  because the data is <em>there to read</em>. IRC keeps the boats
                  close on TCC; ORC tells you which one will be quickest at 14
                  knots true on a 60° true-wind angle.
                </p>
              </div>
            </div>
          </div>
        </section>

        <div className="max-w-4xl mx-auto px-8 sm:px-12">
          <div className="h-px bg-brass/30" />
        </div>

        {/* §4 Where each rule lives */}
        <section className="max-w-3xl mx-auto px-8 sm:px-12 py-10">
          <h2 className="flex items-baseline gap-3 mb-5">
            <span className="data-mono text-brass text-[11px] uppercase tracking-[0.18em]">§4</span>
            <span className="heading-display text-navy text-2xl sm:text-3xl">
              Where each rule lives
            </span>
          </h2>
          <div className="body-text text-charcoal text-[17px] leading-[1.65] max-w-[62ch] space-y-4">
            <p>
              This is the part most pages get wrong. An owner cannot freely pick.
              The rule comes attached to the racing.
            </p>
            <p>
              <strong>IRC dominates</strong> the United Kingdom and Ireland
              (essentially total), the French Atlantic, the Caribbean circuit
              (Antigua Sailing Week, the Heineken Regatta, RORC Caribbean 600),
              Hong Kong, Singapore, Phuket King&rsquo;s Cup, Australia (Rolex Sydney
              Hobart, Hamilton Island Race Week — Hobart scores both), and New
              Zealand. If your calendar runs through any RORC race, an IRC
              certificate is non-negotiable.
            </p>
            <p>
              <strong>ORC dominates</strong> continental Europe broadly — Italy
              unequivocally, Croatia, Greece, the Baltic (Sweden, Finland,
              Estonia, Lithuania, Germany), the Netherlands. The Garmin ORC World
              Championship in Tallinn (8–16 August 2025) and Klaipėda&rsquo;s ORC
              European Championship (7–15 August 2026) are the championship
              anchors. In the United States, ORC has displaced PHRF at
              championship level inshore on the East Coast. Bermuda and Transpac
              sit under ORR — a US-only VPP rule, not ORC, but philosophically a
              cousin.
            </p>
            <p>
              <strong>The Mediterranean is the contested ground.</strong> The
              Rolex Middle Sea Race scores both rules in parallel. Italian
              inshore is ORC. The Giraglia Rolex Cup is ORC. The Spanish and
              French Med dual-score most events. If your campaign crosses
              Gibraltar or Marseille, you will end up holding both certificates
              whether you wanted to or not.
            </p>
          </div>
        </section>

        <div className="max-w-4xl mx-auto px-8 sm:px-12">
          <div className="h-px bg-brass/30" />
        </div>

        {/* §5 The cost */}
        <section className="max-w-3xl mx-auto px-8 sm:px-12 py-10">
          <h2 className="flex items-baseline gap-3 mb-5">
            <span className="data-mono text-brass text-[11px] uppercase tracking-[0.18em]">§5</span>
            <span className="heading-display text-navy text-2xl sm:text-3xl">
              The cost of holding both
            </span>
          </h2>
          <div className="body-text text-charcoal text-[17px] leading-[1.65] max-w-[62ch] space-y-4">
            <p>
              Real numbers, current as of the 2025–26 cycle. Both rules charge by
              hull length, not by flat band, and fees vary by national rule
              authority — what follows is the UK figure unless noted.
            </p>
            <ul className="space-y-3 list-none pl-0">
              <li>
                <strong>IRC Standard certificate</strong>: ~£15.10–£22.20 per
                metre LH for a new application from the RORC Rating Office, so
                roughly £190 for a 12 m boat, £400 for an 18 m boat, £530 for a
                24 m boat. Owner-declared, suitable for club racing and most
                national series.
              </li>
              <li>
                <strong>IRC Endorsed certificate</strong>: measurement at
                £62.40/hour and weighing at £14.60/m LH, plus boatyard, lift and
                travel. There is no published flat figure — most owners budget
                four-figure totals depending on boat size and measurer location.
                Required wherever a Notice of Race or class association
                specifies.
              </li>
              <li>
                <strong>ORC International</strong>: priced per metre by the
                national authority. US Sailing 2025 charges roughly $30–$45/m
                for a new certificate (~$370–$540 for a 12 m boat). UK and
                European authorities sit in similar order of magnitude.
                Measurement, an inclining test and a hull file are required —
                no self-declaration tier for serious racing.
              </li>
              <li>
                <strong>ORC Club</strong>: the lighter-fee option, derived from
                sister-ship data. Pricing varies by national authority — AUD
                $115 flat in Australia, ~$140–$330 for a 12 m boat at US
                Sailing.
              </li>
              <li>
                <strong>Inclining test and hull file</strong>: quoted
                case-by-case by the national rating office or naval architect.
                Typically into four figures.
              </li>
            </ul>
            <p>
              The hidden cost is time. An IRC certificate can be renewed online
              in MyIRC in an afternoon if the boat hasn&rsquo;t changed. An ORC
              International is a measurer&rsquo;s diary entry, an inclining day, and
              a wait. Owners planning a single Mediterranean cameo from a UK
              base routinely under-estimate the ORC lead time and end up racing
              under a provisional certificate they did not optimise.
            </p>
          </div>
        </section>

        <div className="max-w-4xl mx-auto px-8 sm:px-12">
          <div className="h-px bg-brass/30" />
        </div>

        {/* §6 Reading your boat */}
        <section className="max-w-3xl mx-auto px-8 sm:px-12 py-10">
          <h2 className="flex items-baseline gap-3 mb-5">
            <span className="data-mono text-brass text-[11px] uppercase tracking-[0.18em]">§6</span>
            <span className="heading-display text-navy text-2xl sm:text-3xl">
              Reading your boat under the other rule
            </span>
          </h2>
          <div className="body-text text-charcoal text-[17px] leading-[1.65] max-w-[62ch] space-y-4">
            <p>
              The honest answer to &ldquo;what would my J/109 do under ORC&rdquo;
              or &ldquo;what does my Grand Soleil 44 look like in IRC&rdquo; is
              that nobody can tell you in the bar, including the sailmaker who
              fitted the new J3. The rules read different inputs. They surface
              different numbers. The cross-walk is not a coefficient swap — it is
              a re-measurement on paper.
            </p>
            <p>
              This is what The Bench does. We hold the global certificate file
              for both rules, every revision since they were issued. We can show
              you where she sits today on each, where she has drifted across
              rule revisions, and — section by section, ranked by points return
              — what to do about it before the next certificate.
            </p>
          </div>
        </section>

        {/* CTA */}
        <FunnelCTA
          headline={
            <>
              Pull the file on your boat. The Bench reads her under both rules
              and shows where she sits, where she drifts, and what to change.
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
                q: "Is ORC fairer than IRC?",
                a: "Neither rule is fairer — they're optimised for different things. ORC's polar-based scoring is more precise in known wind conditions; PCS in particular is the gold standard for fairness when the race committee has good wind data. IRC is more durable across conditions and across boat ages because no one can game what they cannot see. A 1995 Grand Soleil still wins IRC class regularly. That doesn't happen under any transparent rule.",
              },
              {
                q: "Can I race the Rolex Fastnet Race on an ORC certificate?",
                a: "No. The Rolex Fastnet Race scores under IRC; ORC certificates are not accepted. Two-Handed competes as a sub-class within IRC.",
              },
              {
                q: "What is the difference between TCC and GPH?",
                a: "TCC (Time Correction Coefficient) is IRC's single output — a multiplier you apply to elapsed time. Higher TCC means the rule expects the boat to be faster. GPH (General Purpose Handicap) is ORC's most-cited single number, expressed in seconds per nautical mile, derived from the VPP's predicted times around a windward-leeward course in 8–12 knots. They cannot be converted between each other. They are answers to different questions.",
              },
              {
                q: "My boat rates well in IRC. Will she rate well in ORC?",
                a: "Probably not in the same way. IRC tends to reward stiff, narrow boats with moderate sail area; ORC reads the full hull and rig and credits or penalises specific shapes via the VPP. Light, beamy modern designs with twin rudders often look better under ORC than under IRC. Older masthead cruiser-racers often look better under IRC than under ORC. There is no rule of thumb beyond running the boat through both.",
              },
              {
                q: "Do I need to be re-measured to switch?",
                a: "To switch from IRC Standard to IRC Endorsed: yes — the boat needs an authorised measurer to verify hull, rig, and sails. To get an ORC certificate from scratch: yes — measurement, an inclining test, and a hull file are required, regardless of what IRC papers you already hold. The two rules do not share data.",
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

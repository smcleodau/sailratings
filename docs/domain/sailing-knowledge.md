# Sailing Domain Knowledge Base

> **Purpose.** This document is the foundational knowledge base injected into
> the SailRatings LLM context window. It gives the model "serious sailing
> chops": the measurement, hydrodynamic, aerodynamic, and tactical vocabulary
> needed to reason about boat configurations and race data like a domain expert.
>
> **How to use it.** Read it as ground truth for terminology and relationships.
> Combine it with the structured facts provided in each request's input
> (the "Facts Contract") — never contradict the input numbers, and never invent
> statistics that the input does not contain.

---

## 1. Handicapping & Measurement

### 1.1 The two rating philosophies

SailRatings analyses boats under two dominant international handicap systems.
They are philosophically opposite, and an expert must always name which system
a given number belongs to.

**IRC — the secret, empirical rule.**
IRC is a *measurement* rule administered by the RORC Rating Office and UNCL
(Union Nationale pour la Course au Large). Its central output is the **TCC**
(Time Correction Coefficient). The formula that converts hull, rig, sail, and
stability measurements into TCC is **proprietary and unpublished** — a secret
black box. It is empirically tuned each year by the rule authority based on
fleet performance data.

- Because the formula is secret, **no one outside the rule authority knows the
  exact coefficients or weights.**
- All quantitative claims about how a measurement moves a rating must therefore
  be framed as **empirical deductions** derived from statistical analysis of
  the fleet (e.g. "across 65 boats of this class, increasing displacement by
  100 kg is associated with roughly −2 points of TCC"), never as knowledge of
  the formula itself.
- Each year the rule authority may subtly retune the formula. Boats whose
  measurements are unchanged can still see their TCC shift year-over-year —
  this is "IRC formula drift," detectable only by statistical comparison of
  stable-configuration boats across certificate vintages.

**ORC — the open, VPP-based rule.**
ORC (Offshore Racing Congress) is transparent: it publishes a Velocity
Prediction Program (VPP) that physically models the boat's hydrodynamics and
aerodynamics to predict speeds across a grid of wind speeds and angles. ORC is
**not** a secret — you may reference VPP behaviour, polars, and allowances
directly and openly.

- Primary single-number rating: **GPH** (General Purpose Handicap), expressed in
  seconds per nautical mile. **Lower GPH = a faster boat.**
- Also publishes **OSN** (Offshore Scoring Number), **CDL** (Class Division
  Length), and **triple-number scoring** (Low / Medium / High wind corrected
  times) so that performance can be scored fairly across wind regimes.
- Because the VPP is physical, an expert can reason *mechanistically* about
  ORC boats (e.g. "the polars show her upwind-strong in 12–16 kn, weak running
  deep") in a way that is simply impossible for IRC.

### 1.2 Core rating & measurement parameters

| Term | System | Meaning |
|------|--------|---------|
| **TCC** | IRC | Time Correction Coefficient. A multiplier (~0.8–1.6). Corrected time = elapsed time × TCC. **Lower TCC = a more favourable rating** (the rule assesses the boat as slower for its size). |
| **GPH** | ORC | General Purpose Handicap, seconds per nautical mile. **Lower GPH = faster boat.** |
| **DLR** | Both | Displacement-Length Ratio = displacement / (0.01 × LWL)³. A dimensionless gauge of how heavy a boat is for her waterline length. Higher DLR = heavier for her length = typically more stability but less "sporty" speed potential. |
| **SA/D** | Both | Sail Area to Displacement ratio (non-dimensional). Higher = more sail power per unit of displacement = more powerful, more demanding to sail. Roughly correlates with acceleration and reaching/running horsepower. |

**How configuration changes typically move a rating (empirical tendencies):**
These are *observed fleet tendencies*, framed as deductions — never as known
formula mechanics (especially for IRC).

- **Increasing displacement (weight).** Heavier boats generally rate more
  favourably (lower TCC) in our fleet data, because the rule treats mass as a
  performance limiter. The trade-off: added weight can reduce light-air speed,
  so the rating gain and the real-world speed loss must be weighed together.
- **Increasing draft.** Deeper draft usually *costs* rating (raises TCC) because
  it implies a deeper, higher-aspect keel with more form stability and
  upwind efficiency. Deeper draft is generally associated with better windward
  performance.
- **More declared headsails / spinnakers.** Carrying a larger sail inventory
  tends to cost a point or two of TCC, because the rule charges for the option
  to carry more sail area. Declaring fewer sails is a free/admin way to find
  rating, at the cost of sail-choice flexibility on the water.
- **Larger sails (HLU/HLP, spinnaker dimensions).** Bigger measured sail area
  tends to cost rating, consistent with more horsepower.

> **IRC units & precision convention (SailRatings).** TCC is quoted to **four
> decimal places** (e.g. 1.0250). A "point" = **0.001 TCC**, which equates to
> roughly **3.6 seconds per hour** of racing. Movement is written as a signed
> delta (e.g. +0.0080). GPH is quoted to **two decimals** (613.70); CDL to
> **three**.

### 1.3 Sail & rig measurement terminology

These are the physical dimensions measured off a boat's certificate. They are
the levers an owner can change (with varying cost/feasibility) to move a
rating.

**Rig & foretriangle:**
- **P** — mainsail hoist (luff length along the mast).
- **E** — mainsail foot (along the boom).
- **J** — foretriangle base (deck measurement from mast to forestay).
- **FL** — foretriangle height (forestay length), together with **J** defining
  the rated foretriangle and hence the "J" basis used for sail-size limits.

**Headsails:**
- **HLU** — Headsail Luff (the leading-edge length of the largest measured
  headsail). Longer HLU = a taller headsail.
- **HLP** — Headsail Perpendicular (the shortest distance from the clew to the
  luff); effectively the headsail's "foot projection." Larger HLP = a bigger,
  overlapping headsail.
- Half / three-quarter / upper widths (HHW, HTW, HUW) describe the sail's
  roach profile and are used to compute area and shape.

**Spinnakers:**
- **SPA** — Spinnaker Area (m²), the rated area of the largest downwind sail.
- Symmetric spinnakers: **SLU** (luff), **SLE** (leech), **SF** (foot), **SHW**
  (half width). Asymmetric spinnakers use the same SLU/SLE/SF/SHW labels but
  are cut for apparent-wind angles forward of dead downwind.
- **SPL** — Spinnaker Pole Length; **STL** — Spinnaker pole to forestay
  (tack point projection), the lever that sets how far forward the spinnaker
  can be projected.

**Flying headsails** (a code-zero / furling headsail carried as a separate
"flying" sail): measured with **FLU / FLP** and width bands, plus **FSA**
(flying headsail area).

**Stability:**
- **STIX** — Stability Index, a dimensionless measure of a boat's dynamic
  stability / seaworthiness (affects IRC design category).
- **AVS** — Angle of Vanishing Stability; the heel angle beyond which the boat
  has no positive righting moment.
- **Design category** (0/1/2/3) reflects offshore capability and feeds into
  how the rule treats the boat.

### 1.4 Declaration vs measurement — a key distinction

IRC separates **declarations** (the owner's choice of what to *carry*) from
**measurements** (the physical dimensions that exist). Declarations — e.g.
"2 headsails, 2 spinnakers, 1 flying headsail" — are an owner's free/admin
lever: declare fewer sails and the rating generally improves, at the cost of
sail-choice flexibility. Measurements require re-measurement or physical
change (new sail, keel mod, weight reduction) and are far more expensive.
This distinction underpins the optimiser's feasibility tiers.

---

## 2. Weather, Routing & Polars

### 2.1 VPP and polar plots

A **VPP** (Velocity Prediction Program) balances the hydrodynamic forces on the
hull/appendages against the aerodynamic forces on the sails, solving for the
boat speed and heel/leeway at each combination of wind speed and angle. The
output is a **polar plot**: concentric rings of boat speed (knots) across
radial wind angles (0° dead upwind to 180° dead downwind), with one polar per
wind speed (e.g. 6, 8, 10, 12, 14, 16, 20 kn).

- ORC publishes polars openly; an expert can read them directly.
- For IRC, polars are not part of the rating, but SailRatings can still build or
  reference polar-style performance expectations when discussing how a
  configuration *should* sail.
- **Reading a polar:** the "upwind" lobe points at the best VMG angle (typically
  ~40–50° TWA); the "downwind" lobe points near ~140–170° TWA. The maximum
  radius in any direction is that boat's top speed at that wind strength.

### 2.2 Wind frame-of-reference terminology

Confusing true and apparent wind is the most common amateur error; an expert
never does.

- **TWS — True Wind Speed.** The wind speed over the water (geographic frame).
- **TWA — True Wind Angle.** The angle between the boat's heading and the true
  wind direction. "Sailing at 45° TWA upwind," "bearing away to 150° TWA."
- **AWS — Apparent Wind Speed.** The wind speed felt on the boat — the vector
  sum of true wind and the wind generated by the boat's own motion.
- **AWA — Apparent Wind Angle.** The angle of that apparent wind off the bow.
  AWS and AWA are what the instruments display and what the trimmer actually
  trims to.

Relationship: when sailing upwind, AWS > TWS (boat motion adds to the wind) and
AWA is tighter than TWA. Downwind, AWS < TWS and AWA moves forward as boat speed
increases — this is why asymmetric spinnakers and code-zeros work on wider angles.

### 2.3 VMG and target boat speeds

**VMG — Velocity Made Good.** The component of boat speed in the direction of
the mark: VMG = boat speed × cos(TWA).

- **Upwind VMG** is maximised not at the tightest angle but at the angle where
  speed × cos(TWA) peaks — the "groove." Pinching higher slows the boat and
  *reduces* VMG even though the angle looks better.
- **Downwind VMG** is maximised by sailing hotter (faster) angles and gybing,
  rather than dead downwind — the basis of modern downwind sailing and
  asymmetric spinnaker design.
- **Target boat speeds** are the VPP-derived speeds a well-sailed boat should
  achieve at each TWS/TWA; racing teams use them as benchmarks (e.g. "target
  7.2 kn at 14 kn TWS upwind"). Falling short of target signals trim, sail
  choice, or bottom-fouling problems.

---

## 3. Race Tactics & Maneuvers

### 3.1 Upwind tactics

- **Laylines.** The lines defining the closest angle at which a boat can sail
  to fetch a mark without tacking. The starboard and port laylines converge on
  the windward mark. Overstanding (sailing past the layline) wastes distance;
  understanding (tacking short) requires an extra tack. Layline calls depend
  on current, wind shifts, and the bearing of the mark.
- **Headers and lifts.** A **header** is a wind shift that forces the bow down
  (toward the wind) — bad on the tack you're on, good to tack onto. A **lift**
  is a shift that lets you sail higher — good on the tack you're on. Sailing
  the "lifted tack" first is a foundational upwind principle. Reading oscillating
  vs persistent shifts decides whether to tack on headers or consolidate.
- **Covering.** Staying between a rival and the next mark (a "tight cover")
  to force them into bad air and prevent them from passing. A "loose cover"
  protects a lead while preserving options.

### 3.2 Mark roundings & maneuvers

- **Mark roundings.** Wide-in/narrow-out (or narrow-in/wide-out depending on
  traffic): set up the approach so the boat exits on the favoured side of the
  next leg. The rounding itself costs speed — a clean spinnaker hoist/set on
  the leeward mark or a tight drop at the windward mark is where places are won
  and lost.
- **Tacks and gybes.** Each maneuver costs distance and speed ("tack loss").
  Minimising unnecessary tacks, and executing them crisply, is core to upwind
  efficiency. Gybe sets and peel maneuvers (changing kites) are high-skill,
  high-reward downwind plays.

### 3.3 Sail selection nuances

The right sail for the wind angle and strength is where configuration meets
tactics. An expert reasons about the inventory an owner has *declared*.

**Asymmetric spinnakers (A-sails / "kites")** are sized by their running-mid-girth
ratio and projected area; a common inventory:
- **A0 / FR (Flying Reach)** — the smallest, flattest asymmetric, a tight-angle
  reaching sail for high apparent-wind angles and strong breeze. Used on
  tight reaches where a full kite would be overpowered.
- **A1** — light-air runner/reacher for small angles.
- **A2** — the workhorse VMG runner for medium winds and moderate running
  angles; the most-used downwind sail in a typical inventory.
- **A3** — larger, fuller reaching sail for light-to-medium air on broader
  angles.
- **A4 / A5** — big running sails for heavy / deep-running conditions.

**A0 vs A2** (a frequently asked trade-off): choose the **A0** when the leg is
a tight reach in stronger breeze where a full **A2** would be overpowered and
force the boat to sail too low; choose the **A2** for true running legs where
max projected area and a deeper sailing angle maximise VMG.

**Code Zero vs Jib** (upwind/reaching crossover):
- A **Code Zero** (a tight-luffed, free-flying furling headsail) is a reaching
  sail — it shines on close reaches in light-to-medium air where the boat is
  under-powered and a wider apparent-wind angle than a jib allows more sail area
  to be flown. It is *not* an upwind sail in breeze.
- A **Jib** (the standard tacked-on headsail, measured by HLU/HLP) is the
  windward sail — highest efficiency sailing close to the wind. As the wind
  frees (bearing away onto a reach) and lightens, the crossover to a Code Zero
  lets the boat carry more area without over-pressuring.

**Symmetric vs asymmetric:** symmetric spinnakers (projected on a pole) still
matter for dead-downwind and some one-design/cruiser-racer classes; modern boats
fly asymmetric kites off a bowsprit and sail hotter, faster angles, gybing
downwind rather than running dead.

### 3.4 Putting it together — config meets tactics

Rating optimisation is only worth doing if it helps on the water. The
expert's job is to connect a measurement change to a tactical consequence:
a smaller headsail (lower HLP) that saves rating points must still cover the
wind range the boat actually races in; a deeper keel that costs rating must
deliver the upwind VMG to justify it. Trial certificates let an owner test a
"what if" configuration against the rule before committing — always recommend
one to confirm a predicted rating change.

---

## 4. Grounding in SailRatings data & engines

This knowledge must be applied to the structured facts provided in each
request. SailRatings' analysis engines produce the empirical evidence the
expert cites:

- **Regression (Engine 1)** — within-class measurement sensitivity: how each
  dimension (displacement, draft, HLU, HLP, …) associates with TCC, framed as
  fleet patterns, never as known formula weights. Reported with model tier
  (A/B/C) and quality statistics.
- **Temporal drift (Engine 2)** — IRC formula drift year-over-year for
  stable-configuration boats; how the rule's treatment of each dimension has
  shifted.
- **RAI / performance (Engine 3)** — Racing Advantage Index: whether a boat
  out- or under-performs its TCC on corrected time, plus head-to-head rival
  records.
- **Optimiser (Engine 4)** — ranked, actionable recommendations with estimated
  TCC delta, feasibility tier, and evidence strength.
- **Design compare (Engine 5)** — cross-design-class comparison.

**Facts Contract (binding).** The numbers in the request's input
(JSON/Dataclass context) are the only statistics the expert may cite. Every
rating value, sail number, certificate count, sister-boat count, race count,
coefficient, delta, and percentile must be reproduced **exactly** as provided.
Inventing figures, rounding inconsistently, or pulling numbers from memory
rather than the input is a hard error. If a needed number is absent, restructure
the answer around what *is* present — never fabricate.

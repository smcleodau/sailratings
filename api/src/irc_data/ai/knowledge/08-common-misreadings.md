# 08 — Common misreadings

> Review status: DRAFT — awaiting Stuart's review (Human Gate, card AI-01-01).
> Each item is a mistake a persona must not make, with the correct statement.

1. **"Lower TCC is a worse boat."** Wrong. A lower TCC means the rule rates her as slower and she gets more time on corrected time. It says nothing about how well she is sailed.
2. **"Rank the boat by raw TCC."** Wrong. Boats at different TCC levels race in different classes at events. Do not say "ranked #160 of 187 by TCC".
3. **"Higher TCC means back of the fleet."** Wrong. A higher TCC means a different configuration (more sails declared, bigger rig). Whether it is good depends on results at that rating.
4. **"A point is a full TCC unit."** Wrong. One point is **0.001 TCC**, which is **3.6 seconds per hour**.
5. **"TCC and GPH are comparable."** Wrong. TCC is a multiplier near 1.000; GPH is seconds per nautical mile. Do not compare or convert them by arithmetic.
6. **"IRC penalises X."** Wrong. The IRC formula is confidential. Say "consistent with the fleet data" or "tends to cost a point or two across similar boats". Never claim to know coefficients.
7. **"TCC penalty."** Do not use it. Say "rating impact" or "costs X points".
8. **"No GPH means no ORC rating."** Wrong. It usually means the detail page has not been fetched yet.
9. **"The certificate issue date is the rule year."** Wrong. Use the certificate (cert) year for the rule year.
10. **"A secondary certificate is a second boat."** Wrong. A secondary certificate is another certificate for the same boat (for example short-handed), not a different boat.
11. **"Endorsed means better."** Wrong. Endorsed means the measurement data has been verified; it does not mean faster or slower.
12. **"Weight" versus "displacement".** Use **displacement**, not "weight".
13. **"A drift of any size is real."** Wrong. Drift is observed only above 0.001 TCC mean change.
14. **"A rating change is a performance change."** Wrong. A rating change can be re-measurement, declaration, or a rule-year change with no change in how the boat sails.
15. **"Invented numbers are fine if plausible."** Wrong. Only cite numbers that appear in the FACTS payload; otherwise omit the sentence.
16. **Cross-country comparison.** Do not compare an Australian boat to a UK or Japanese boat when local fleet data exists; they never meet on the water.

## Sources

- RORC Rating Office / UNCL, *IRC Rating Rule* and IRC FAQ — https://www.ircrating.org
- Offshore Racing Congress, *ORC International Rating Rule* — https://orc.org
- Platform conventions: report prompts (`api/src/irc_data/api/services/report/prompts.py`, `api/src/irc_data/api/services/insights_service.py`) and `docs/specs/SPEC-08-Sailing-AI.md` §3 (Empirical Deduction and Truth Discipline).

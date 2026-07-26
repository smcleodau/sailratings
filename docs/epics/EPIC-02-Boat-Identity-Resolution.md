# EPIC-02: Boat Identity Resolution & Adversarial Analysis

## Problem Statement
Currently, boat matching during ingestion is often brittle and deterministic, relying on basic name or sail number matching (e.g., `_find_boat_by_name`, `find_boat_by_sail_number` in `news.py` and `result_import.py`). The current implementation fails under adversarial or real-world messy conditions:
- **Alias Issues**: "Wild Oats XI" vs "WOXI" vs "Wild Oats 11".
- **Sail Number Variations**: "GBR8994R" vs "8994" vs "GBR-8994-R".
- **Re-flagging/Re-numbering**: Boats frequently change sail numbers or countries.
- **Name Collisions**: Common names like "Sunrise" or "Rampage" applied to completely different hull designs.
When the existing simple heuristics fail, data is either lost (unmatched) or incorrectly clustered, poisoning the database.

## Technical Approach
1. **Centralized Identity Service**: Build a unified entity resolution module that scores candidate matches using weighted factors (Name, Sail Number, Design, LOA, TCC rating).
2. **Fuzzy Matching & Aliasing**: Implement semantic matching and phonetic algorithms for names, along with explicit alias mapping (e.g., expanding "WOXI" to "Wild Oats XI").
3. **Temporal Validity**: Identity resolution must account for time. A sail number assigned to a boat in 2021 might belong to a different boat in 2023.
4. **Human-in-the-Loop Validation**: Ambiguous matches (confidence score between 0.4 and 0.8) should be flagged in an administrative UI for manual resolution rather than automatically discarding or merging.

## Acceptance Criteria
- [ ] Document specific edge cases in a test suite (e.g., same name / different design, changed sail numbers).
- [ ] Implement a scoring-based Identity Resolution engine replacing direct `WHERE boat_name = X` queries.
- [ ] Update the data ingestion pipeline (from EPIC-01) to route unmatched or low-confidence records to a review queue.
- [ ] Provide backward compatibility for existing `boat_identities` tracker table.

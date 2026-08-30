# SPEC-08: Sailing AI Domain Expert (System Prompts & Context)

## 1. Overview
The SailRatings platform relies on LLMs (like GLM 5.2) to analyze complex data (like regression coefficients, TCC drift, and race results) and generate expert-level prose for the Substantial Premium Report. To prevent hallucination and ensure the AI sounds like a true domain expert, we must explicitly define its persona, its rules of engagement, and provide it with a massive, foundational knowledge base.

## 2. The Domain Knowledge Base (`docs/domain/sailing-knowledge.md`)

The agent must create a comprehensive, highly detailed markdown file at `docs/domain/sailing-knowledge.md`. This file will be injected into the LLM's context window. It MUST cover all of the following:

### 2.1 Handicapping & Measurement
- Definitions of **IRC** (secret, empirical) and **ORC** (open, VPP-based) rating systems.
- Explanations of core parameters: **TCC** (Time Corrector), **GPH** (General Purpose Handicap), **DLR** (Displacement-Length Ratio), **SA/D** (Sail Area to Displacement Ratio).
- Sail measurement terminology: **HLU** (Headsail Luff), **HLP** (Headsail Perpendicular), **SPA** (Spinnaker Area), **P**, **E**, **J**, **FL**.
- Explain how changes in displacement (weight) or draft generally affect a rating.

### 2.2 Weather, Routing & Polars
- Definitions of **VPP** (Velocity Prediction Program) and Polar Plots.
- Explanation of True Wind Speed (**TWS**), True Wind Angle (**TWA**), Apparent Wind Speed (**AWS**), Apparent Wind Angle (**AWA**).
- Concepts of VMG (Velocity Made Good) and target boat speeds.

### 2.3 Race Tactics & Maneuvers
- Core tactics: Laylines, mark roundings, covering, headers, and lifts.
- Sail selection nuances (e.g., when to use an A0 vs A2 spinnaker, Code Zero vs Jib).

---

## 3. The System Prompt Persona

The agent must draft the actual system prompts (e.g., in `api/src/irc_data/api/services/report/prompts.py`). The LLM must be instructed to adopt the following persona:

- **Tone:** The "Technical Expert & Tactician". Authoritative, highly analytical, and deeply knowledgeable. Uses proper terminology (e.g., 'wetted surface', 'form stability', 'VPP') but keeps the final prose accessible and actionable.
- **Handling IRC Secrecy (Empirical Deduction):** The LLM MUST be explicitly instructed that the IRC rating rule is a secret black-box. It must NEVER claim to know the exact formula. Instead, it must frame all insights as being derived empirically from our statistical fleet regressions (e.g., *"Our statistical analysis of the fleet indicates that increasing displacement by 100kg typically yields..."*).
- **Truth Discipline:** The prompt must reiterate the "Facts Contract" pattern. The LLM must only cite numbers provided to it in its input JSON/Dataclass and is strictly forbidden from inventing statistics.

## 4. Acceptance Criteria
- [ ] `docs/domain/sailing-knowledge.md` is created and thoroughly covers Handicapping, Weather, and Tactics.
- [ ] A new system prompt string is drafted (e.g., `SYSTEM_PROMPT_EXPERT`) that explicitly instructs the LLM to adopt the "Technical Expert & Tactician" persona.
- [ ] The system prompt explicitly enforces the "Empirical Deduction" rule regarding the secret nature of the IRC formula.

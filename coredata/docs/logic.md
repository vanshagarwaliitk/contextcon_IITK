Algorithm Specification: Deterministic Evidence Scoring

1. Individual Signal Weighting
Calculate a baseline weight for every raw signal before clustering, using the following parameters:

Distinct Source Quality (Base Value): Assign higher base values to primary, high-impact signals (e.g., executive leadership hires, Crustdata firmographic changes) and lower base values to secondary signals (e.g., generic job postings).

Recency Factor (Time-Decay): Apply a time-decay multiplier based on the retrieved or observed date. For example, signals observed within the last 30 days retain 100% weight (1.0x), while signals over six months old drop to 50% weight (0.5x).

Verification Multiplier:

Corroborated (2+ independent credible sources): 1.5x or 2.0x multiplier.

Single-source: 1.0x multiplier.

Unverified: 0 weight (excluded from calculations).

2. Thematic Clustering & Aggregation
Once signals are programmatically clustered by relational themes, calculate the overall hypothesis score:

Positive Score: Sum the final weighted values of all distinct supporting signals tied to the specific cluster.

Counter-Evidence Penalty: Identify any signals labeled as "Conflicting" (evidence pointing in a contradictory direction). Subtract the weighted value of this counter-evidence from the Positive Score to determine the net confidence score.

3. Architectural Guardrail: Python vs. LLM

Deterministic Math (Python): All calculations, signal clustering, and confidence threshold mapping (Low/Medium/High) must be executed natively in Python.

LLM Restriction: To eliminate hallucination, the LLM is strictly restricted to acting as a writer. Feed the LLM the pre-calculated score and the clustered data solely to generate the textual explanation of the pattern. Never prompt the LLM to analyze signals to determine its own confidence score.

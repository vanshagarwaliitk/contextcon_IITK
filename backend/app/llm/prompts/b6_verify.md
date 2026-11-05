You verify whether claims are supported by their cited evidence. Be strict.

CLAIMS, each with its type and the stored text of every cited signal:
{{claims_with_evidence}}

For EACH claim return {claim_id, verdict, reason}. verdict:
- supported: every specific element of the claim (entities, numbers, dates, direction) is stated in the evidence
- partial: the evidence supports part of the claim or a weaker version
- unsupported: the evidence does not state the claim
- contradicted: the evidence conflicts with the claim
For claim_type = hypothesis, judge only whether the cited evidence is consistent with the stated reasoning and that the wording does not overstate (e.g. "will", "is building").
For claim_type = recommendation, judge only whether it follows from the cited evidence gaps / hypotheses.
reason <= 20 words. Use only the evidence shown; ignore anything you may know about the company.

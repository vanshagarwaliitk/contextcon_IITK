"""Signal factors from coredata/docs/test_factors.md, expressed as Crustdata query constraints.

Each JobFactor adds filters to a JobQueryBuilder, so the factor is applied *inside* the Crustdata query
(limit:0 counts — never rows). The collector runs every factor automatically:
  * company level, recent + prior windows (baseline stage)
  * theme level, recent window, for themes whose rows were fetched (end of the probe stage)
features.py turns the counts into derived_metric signals and falsify.py tests them. All maths is Python.

Factors in test_factors.md that Crustdata job/person search cannot express (CT-log subdomains, GitHub,
BuiltWith, ad libraries, review sites, USPTO, #OpenToWork, time-to-fill) are NOT queried; the
commercial/legal page factors (pricing, sub-processors, trademarks, changelog) go through web search only.
The doc's "90%+ confidence / within 60 days" style of prediction is deliberately not adopted: strength is
the rule-based Low/Medium/High in score.py.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ..providers.crustdata.jobs import JobQueryBuilder

# Postings whose category is legal/compliance are excluded from the compliance factor:
# a lawyer mentioning GDPR is not a signal (test_factors.md §1 "in non-legal roles").
LEGAL_CATEGORIES = ["Legal", "Compliance", "Legal & Compliance", "Risk & Compliance"]

COMPLIANCE_PHRASES = ["HIPAA", "SOC 2", "SOC2", "FedRAMP", "PCI DSS", "PCI-DSS", "GDPR", "ISO 27001"]
ZERO_TO_ONE_TITLES = ["Founding", "Head of", "Principal", "Staff", "Director", "New Initiatives"]
JUNIOR_TITLES = ["Junior", "Intern", "Graduate", "Entry Level", "Associate", "QA", "Test Engineer"]
GTM_TITLES = ["Account Executive", "Solutions Architect", "Solutions Engineer", "Sales Engineer",
              "Customer Success", "Sales", "Business Development", "Partnerships", "Marketing"]
RND_TITLES = ["Software Engineer", "Developer", "Research", "Scientist", "Machine Learning",
              "Product Manager", "Platform Engineer", "Backend", "Frontend"]


@dataclass(frozen=True)
class JobFactor:
    id: str
    doc_ref: str                      # section of test_factors.md
    description: str                  # what the query counts (shown in computation text)
    apply: Callable[[JobQueryBuilder], JobQueryBuilder]
    theme_scoped: bool                # also run inside the theme filter


JOB_FACTORS: list[JobFactor] = [
    JobFactor("compliance", "§1 Domain & Compliance Keywords",
              f"non-legal postings whose description contains any of {COMPLIANCE_PHRASES}",
              lambda qb: qb.description_any(COMPLIANCE_PHRASES).exclude_categories(LEGAL_CATEGORIES), True),
    JobFactor("zero_to_one", "§1 Seniority Ratio (zero-to-one)",
              f"postings whose title contains any of {ZERO_TO_ONE_TITLES}",
              lambda qb: qb.title_any(ZERO_TO_ONE_TITLES), True),
    JobFactor("junior", "§1 Seniority Ratio (scale/maintenance)",
              f"postings whose title contains any of {JUNIOR_TITLES}",
              lambda qb: qb.title_any(JUNIOR_TITLES), False),
    JobFactor("gtm", "§1 Departmental Ratios (GTM)",
              f"postings whose title contains any of {GTM_TITLES}",
              lambda qb: qb.title_any(GTM_TITLES), True),
    JobFactor("rnd", "§1 Departmental Ratios (R&D)",
              f"postings whose title contains any of {RND_TITLES}",
              lambda qb: qb.title_any(RND_TITLES), False),
    JobFactor("reposted", "§1 Re-postings & Time-to-Fill",
              "postings flagged job_details.reposted_job = true",
              lambda qb: qb.reposted(True), False),
]
FACTOR_BY_ID = {f.id: f for f in JOB_FACTORS}
COMPANY_WINDOWS = ("recent", "prior")

# §3 Commercial & §4 Legal signals that are reachable through Crustdata web search.
WEB_FACTOR_QUERIES: list[tuple[str, str]] = [
    ("pricing", "{company} {theme} pricing plans enterprise"),
    ("subprocessors", "{company} sub-processors list {theme}"),
    ("trademark", "{company} {theme} trademark application"),
    ("changelog", "{company} {theme} changelog API"),
]

# Which extra falsification tests apply to which hypothesis types (auto-added in falsify.select_tests).
TESTS_FOR_TYPE: dict[str, list[str]] = {
    "go_to_market_shift": ["gtm_test"],
    "new_customer_segment": ["compliance_test", "gtm_test"],
    "new_product_category": ["zero_to_one_test"],
    "capability_buildout": ["zero_to_one_test"],
    "contraction_or_cost_cutting": ["scale_phase_test"],
}


# ------------------------------------------------------------------ pure math (unit-tested)
def ratio(a: int | None, b: int | None) -> float | None:
    """a / (a + b); None when either side is missing or both are zero."""
    if a is None or b is None or (a + b) == 0:
        return None
    return round(a / (a + b), 3)


def share(n: int | None, total: int | None) -> float | None:
    if n is None or not total:
        return None
    return round(n / total, 3)

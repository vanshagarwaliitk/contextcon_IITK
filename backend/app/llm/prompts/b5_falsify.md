You select tests that could DISPROVE a hypothesis. You do not run them.

HYPOTHESIS: {{hypothesis}}
AVAILABLE TESTS (ids and what they check):
- novelty_test: was the theme share already similar 4-12 months ago?
- evergreen_test: share of reposted / duplicated postings
- scale_test: is the theme a tiny share of total postings, or fewer than 3 postings?
- peer_test: do competitors hire for the same theme at a similar share?
- seniority_test: are any senior / lead roles present?
- geo_test: do recent theme postings appear in countries not seen before? (new_geography only)
- corroboration_test: is there a primary-source web page (company domain) for the theme?
- source_diversity_test: how many distinct job platforms back the supporting postings?
- gtm_test: did the GTM-vs-R&D posting mix shift toward GTM? (go_to_market_shift, new_customer_segment)
- compliance_test: do non-legal postings name HIPAA / SOC 2 / FedRAMP / PCI DSS / GDPR / ISO 27001? (new_customer_segment)
- zero_to_one_test: are there founding / head-of / principal titles in the theme? (new_product_category, capability_buildout)
- scale_phase_test: is hiring junior-heavy, i.e. a scaling phase rather than contraction? (contraction_or_cost_cutting)
SIGNAL COVERAGE SO FAR: {{coverage}}

Return `tests`: a list of {test_id, reason}. Choose every test relevant to this hypothesis_type (e.g. geo_test only for new_geography). Always include novelty_test, evergreen_test, scale_test and peer_test. Use only listed test ids.
Return `disconfirming_web_queries`: up to 2 short web search queries designed to find DISCONFIRMING public evidence (e.g. company statements that the roles support an existing product). May be empty.
No analysis text.

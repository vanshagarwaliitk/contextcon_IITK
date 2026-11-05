# SignalSleuth — Architecture + Claude Code Prompt Playbook

Repo mein `docs/PLAYBOOK.md` ke naam se rakho, aur `CLAUDE.md` repo root mein. PRD ko `docs/PRD.md` mein copy kar do.
Prompts English mein hain (Claude Code ko precise instructions chahiye); explanation Hinglish mein.

**Verified Crustdata docs (API version 2025-11-01):**
- Full docs index: https://docs.crustdata.com/llms.txt
- Job search: https://docs.crustdata.com/job-docs/search/introduction  ·  reference: https://docs.crustdata.com/job-docs/search/reference.md
- Job live search: https://docs.crustdata.com/job-docs/search/live-search.md  ·  autocomplete: https://docs.crustdata.com/job-docs/autocomplete/introduction.md
- Company identify: https://docs.crustdata.com/company-docs/identify/introduction  ·  reference: https://docs.crustdata.com/company-docs/identify/reference
- Company enrich: https://docs.crustdata.com/company-docs/enrichment/introduction  ·  reference: https://docs.crustdata.com/company-docs/enrichment/reference.md
- Person search: https://docs.crustdata.com/person-docs/search/introduction  ·  reference: https://docs.crustdata.com/person-docs/search/reference
- Web search/fetch (docs home se linked): https://docs.crustdata.com/web-docs/search/introduction
- Crustdata MCP (Claude Code mein attach karke hour-1 exploration fast hoga): https://docs.crustdata.com/general/mcp
- Pricing: https://docs.crustdata.com/general/pricing.md

> Ye docs public API ke hain. Tumhare hackathon account mein kaunse endpoints/credits allowed hain, wo PRD ke hisaab se confirm karna padega. A3 (discovery script) isi ke liye hai.

---

# PART 0 — Architecture

## 0.1 Core design decision

PRD ka "single tool-calling orchestrator" ko ek **staged deterministic pipeline** banao, jisme LLM sirf 5 bounded jagah use hota hai. Free-roaming agent ek raat mein flaky hoga.

| Stage | Kaun karta hai | Output |
|---|---|---|
| 1 Scoping | LLM (+ data-driven themes) | themes: keywords, anti-keywords, role families |
| 2 Resolve | Python | confirmed company entity |
| 3 Baseline collect | Python | aggregations: category/title/country/workplace x time windows |
| 4 Probe loop | **LLM planner (budgeted)** + Python executor | theme counts, rows, tech, geo, people, web evidence |
| 5 Normalise + features | Python | signals + computed features (velocity, concentration, seniority, evergreen ratio) |
| 6 Cluster | Python candidates + LLM naming/merge | clusters of signal IDs |
| 7 Hypothesise | **LLM (competing hypotheses)** | hypotheses + evidence matrix |
| 8 Falsify | LLM picks tests, **Python executes** | contradicting / neutral signals |
| 9 Score | Python only | Low / Medium / High |
| 10 Verify + Brief | LLM judge + writer, Python validators | claim ledger -> report |

## 0.2 Flow

```
Intake -> Scoping(LLM) -> Resolve(/company/identify) -> Enrich(/company/enrich)
   -> Baseline aggregations (/job/search, limit:0, time windows)  -> emerging-terms detector
   -> Probe loop [Planner LLM picks probe -> Executor runs -> Ledger updates]  (budget: N calls / credits)
        probes: job_theme_count | job_theme_rows | job_tech | job_geo | peer_baseline | people_leadership | web_evidence
   -> Normaliser -> signals table (+ derived signals w/ computation trace)
   -> Features (Python) -> Clusterer -> Hypothesis engine (ACH: primary vs benign vs null)
   -> Falsifier (test registry, Python) -> Scorer (Python) -> Claim ledger + Verifier -> Brief (HTML/PDF)
```

## 0.3 Signal correlation ka asli logic (differentiator)

Ek posting = weak. **Delta + concentration + diversity** = signal.

- **Velocity:** theme postings last 30d vs prior 90d (per-day rate). Level nahi, change dekho.
- **Novelty:** kya ye theme 6-12 mahine pehle bhi utna hi tha? Agar haan -> "emerging" claim kamzor.
- **Concentration:** theme postings / total postings. 3 robotics roles out of 800 = noise.
- **Seniority mix:** lead/staff/principal/director/head hona = capability build-out ka stronger hint.
- **Geo spread:** naye country mein postings = geography expansion hypothesis.
- **Peer contrast:** agar competitor set mein sab same theme hire kar rahe hain -> industry trend, company-specific move nahi.
- **Evergreen/duplicate filter:** `job_details.reposted_job`, same title x many locations, description hash.
- **Type diversity:** jobs + people + web. Sirf jobs = High nahi ho sakta.

## 0.4 Competing hypotheses (ACH-lite)

Har cluster ke liye 3 rival explanations: **H1** primary strategic move, **H2** benign/alternative (existing product support, backfill, industry trend), **H3** noise (evergreen, aggregator duplicates). LLM har signal ko har hypothesis ke saath `consistent / inconsistent / neutral` label karta hai. Jo signal teeno ke saath consistent hai wo diagnostic nahi. Score Python deta hai; agar koi alternative saare supporting signals ke saath equally consistent hai -> score Medium par cap.

## 0.5 Evidence status (PRD 6.2) ka implementation rule

- Ek provider (Crustdata) ke andar alag `job_details.source` platforms (greenhouse, lever, professional_network...) = alag sources.
- **Corroborated** = 2+ distinct underlying sources (alag job platforms, ya Crustdata + web primary page).
- Sirf ek Crustdata record = **Single-source**.
- LLM ka output hamesha **Hypothesis**, kabhi fact nahi.

## 0.6 Repo layout

```
signalsleuth/
  CLAUDE.md
  docs/{PRD.md, PLAYBOOK.md, crustdata_findings.md}
  fixtures/                    # recorded real API responses (replay mode)
  scripts/{discover.py, run_investigation.py}
  backend/app/
    main.py config.py db.py schema.sql models.py
    providers/crustdata/{client.py, company.py, jobs.py, people.py, web.py, replay.py}
    pipeline/{scoping, resolve, collect, planner, normalise, features, cluster,
              hypothesize, falsify, score, verify, brief, runner}.py
    llm/{client.py, schemas.py, prompts/*.md}
    tests/
  frontend/
```

## 0.7 Extra tables (PRD ke 5 tables ke upar)

`themes`, `probes` (params_json, status, rows, credits_est, signal_ids), `signal_features`, `clusters`, `cluster_signals`, `hypothesis_matrix` (hypothesis_ref, signal_id, relation), `tests` (falsifier results), `claims`, `claim_signals`, `events` (UI live timeline), `provider_mode` column on investigations/signals.

## 0.8 Build order (critical path)

Data -> signals -> clusters -> hypothesis -> falsify/score -> brief -> UI. UI sabse last. Hour 4 par agar peeche ho: people probe + web probe drop, company + job signals hi rakho (PRD cut line).

---

# PART A — Claude Code build prompts (is order mein paste karo)

**Workflow tips:** Har prompt Plan mode mein start karo, plan approve karo, phir implement. Har prompt ke baad `pytest -q` + commit (PRD checklist: meaningful commit history). "Done when" check Claude Code se khud run karwao.

## A0 — Bootstrap

````
Read docs/PRD.md, CLAUDE.md and docs/PLAYBOOK.md fully. Then create the repo skeleton exactly as in PLAYBOOK section 0.6: Python 3.11 venv, pyproject/requirements (fastapi, uvicorn, httpx, pydantic>=2, anthropic, pytest, respx, python-dotenv, weasyprint or playwright for PDF), Makefile (dev, test), .env.example (CRUSTDATA_API_KEY, ANTHROPIC_API_KEY, LLM_MODEL, PROVIDER_MODE=live, CREDIT_BUDGET, MAX_PROBES), .gitignore (.env, *.db). Do not write business logic yet. Make the first commit.
Done when: `make test` runs (even with 0 tests) and `uvicorn app.main:app` starts with GET /health.
````

## A1 — Schema + models

````
Implement backend/app/schema.sql and models.py (pydantic v2) per PRD section 9 PLUS the extra tables in PLAYBOOK 0.7.
Requirements:
- Signal model fields: id (sig_001 style, per investigation), investigation_id, signal_type (hiring_cluster|leadership_change|workforce_movement|company_change|web_evidence|derived_metric), title, summary, observed_at (nullable), source_name, source_url (nullable), provider, endpoint, raw_ref, retrieved_at, provider_mode (live|replay|seeded), derived (bool), derived_from (list of signal ids), computation (text: how a derived metric was computed), theme_tags (list), features (json).
- Hypothesis model must match PRD Appendix B and add: hypothesis_type enum (new_product_category|new_geography|new_customer_segment|capability_buildout|go_to_market_shift|leadership_or_org_change|contraction_or_cost_cutting|insufficient_evidence), alternatives (list of {statement, would_be_supported_by, consistent_signal_ids}), score_breakdown (json).
- Evidence status enum exactly: corroborated|single_source|conflicting|unverified|hypothesis.
- db.py: sqlite3 helper with WAL mode, migrations on startup, id generator, `emit_event(investigation_id, stage, level, message, payload)`.
Write pytest tests for model validation and id generation.
Done when: tests pass and a script can create an investigation row and 3 signals.
````

## A2 — Crustdata client core (record / replay / budget)

````
Build backend/app/providers/crustdata/client.py.
Requirements:
1. httpx client with base_url https://api.crustdata.com and headers Authorization: Bearer <CRUSTDATA_API_KEY> and x-api-version: 2025-11-01 on every request. Timeout 30s.
2. One method `post(endpoint, body, investigation_id, label)` that: (a) checks the in-process budget, (b) checks a per-investigation cache keyed by sha256(endpoint+canonical json body), (c) in replay mode serves from fixtures/<sha>.json and raises ReplayMiss if absent, (d) in live mode calls the API and, if RECORD=1, writes the response to fixtures/, (e) logs to api_logs (provider, endpoint, status_code, credits_estimate, error, latency).
3. Retry: only for 429 and 5xx, max 2 retries, exponential backoff with jitter. NEVER retry a 500 whose message contains "Unsupported" (that is our bug) or any 400/401/403/404. Raise typed errors: CrustdataAuthError, CrustdataBadRequest, CrustdataPermission, CrustdataNotFound, CrustdataServerError.
4. Budget class: max_calls and max_credits_est from env; `charge(endpoint, rows_returned)` using a config table (job/search: 1 credit/row returned [unverified for limit:0 aggregations — default 0 and flag], person/search: 0.03/row, company/identify: 0, web search/fetch: 1, enrich: configurable). Raise BudgetExceeded.
5. Never log the API key. 
Write tests with respx: header presence, retry behavior, no-retry on "Unsupported", cache hit, replay miss, budget exceeded.
Done when: all tests pass.
````

## A3 — API discovery script (HOUR 1, sabse important)

````
Create scripts/discover.py: `python -m scripts.discover <domain> [--theme "robotics"] [--record]`.
Purpose: empirically confirm Crustdata schemas and costs for MY account before building adapters. Do NOT guess; fetch these doc pages first with WebFetch and read them: https://docs.crustdata.com/job-docs/search/reference.md, https://docs.crustdata.com/company-docs/identify/reference, https://docs.crustdata.com/company-docs/enrichment/reference.md, https://docs.crustdata.com/person-docs/search/reference, https://docs.crustdata.com/web-docs/search/introduction.
Steps the script must run and print (pretty JSON, truncated) for the given domain:
 1. POST /company/identify {"domains":[domain]}  -> print raw shape; detect whether response is a top-level array or {"results":[...]}.
 2. POST /company/enrich with the crustdata_company_id and a small `fields` list; print which sections exist (headcount, funding, etc.).
 3. POST /job/search count: filter company id + metadata.date_added => {30 days ago}, limit 0, aggregations [{"type":"count"}]. Print total_count AND aggregation value.
 4. POST /job/search group_by job_details.category, job_details.title (size 30), location.country, job_details.workplace_type, job_details.source — limit 0.
 5. POST /job/search theme probe: company id AND date window AND OR-group of {content.description [.] "<theme>"} and {job_details.title (.) "<theme>"}; limit 5; fields = recommended default set from the docs (title, category, url, company name/domain, location.raw, metadata.date_added) plus job_details.reposted_job and job_details.source.
 6. POST /job/search/autocomplete for field job_details.title with query "<theme>".
 7. POST /person/search: current employer = company, title contains a senior keyword, limit 3, minimal `fields` (read the reference first to pick correct field paths, including any start-date/recent-change field).
 8. Crustdata web search for "<company> <theme>" (read the web-docs page for the correct endpoint/body) and one web fetch of the top result.
For each call record: HTTP status, latency, response keys, and credits consumed (if an account-credit endpoint exists, call it before/after; otherwise note "unknown").
Write docs/crustdata_findings.md with: confirmed request/response shapes, field paths that worked, which endpoints are permitted on this account, observed credit cost per call type, anything that differed from the docs. With --record, save every response into fixtures/.
Done when: findings.md exists and every adapter assumption in later prompts can be checked against it.
````

## A4 — Company resolver + enrich

````
Read docs/crustdata_findings.md first. Implement providers/crustdata/company.py and pipeline/resolve.py.
- identify(name?, domain?): prefer domain; if only name, send names=[name]. Accept both response envelopes. Normalise to Candidate(crustdata_company_id, name, primary_domain, confidence_score, extra basic_info).
- Resolution policy (deterministic): if domain provided and a candidate's primary_domain equals it -> status "matched". If name-only: if exactly one candidate OR top candidate leads the second by a clear margin AND primary_domain exists -> "needs_confirmation" (always show to user). Otherwise "ambiguous" -> pipeline stops with a clear event and the UI asks the user to pick/correct. Never auto-continue on ambiguous. Do NOT threshold on absolute confidence_score (scale is inconsistent in docs).
- enrich(crustdata_company_id, fields=[...]): store profile; missing -> null.
- Persist resolved_company_id on investigations, store the raw payload ref, create `company_change`/profile signals ONLY for fields that are actually returned (e.g. headcount, funding last_fundraise_date) with source_url null if none, and provider/endpoint/retrieved_at set.
- Competitor set: resolve each competitor the same way (identify only; enrich only if budget allows) because peer baselines need their company ids.
Tests: ambiguous-name fixture stops the pipeline; domain match proceeds; both envelopes parse.
Done when: running on the demo domain stores a profile and emits events.
````

## A5 — Job collector (baseline + theme probes) — SABSE IMPORTANT DATA PROMPT

````
Implement providers/crustdata/jobs.py and pipeline/collect.py. Re-read the job reference (https://docs.crustdata.com/job-docs/search/reference.md) and docs/crustdata_findings.md. Use "field" (not "column"), operators => and =< (not >= <=), [.] for exact phrases, (.) for all-words, nested and/or groups.
Build a JobQueryBuilder with typed helpers so no raw filter dicts are scattered in code:
  company(company_id), window(start, end) using metadata.date_added (=> inclusive start, < exclusive end), theme_match(theme) -> OR( content.description [.] each description_phrase, job_details.title (.) each title_keyword ), exclude anti_keywords (use not-match logic via a client-side post-filter if the API cannot express it), category(cat), country(c), technologies(names), reposted(bool).
Windows (configurable, computed from today's date at runtime): recent = last 30d; prior = 31-120d; older = 121-365d.
Functions:
 1. baseline(company_id): for each window run limit:0 aggregations: count; group_by job_details.category; group_by job_details.title (size 50); group_by location.country; group_by job_details.workplace_type; group_by job_details.source. Store as derived_metric signals with computation text and the exact request body in raw_ref.
 2. theme_count(company_id, theme, window): limit:0 count. Use total_count or aggregation value (whichever findings.md shows works).
 3. theme_rows(company_id, theme, window, limit<=50): fetch rows only if theme_count >= MIN_ROWS_THRESHOLD (default 2). fields = job_details.title, category, url, reposted_job, source, number_of_openings, location.raw/city/country, metadata.date_added, metadata.date_updated, company.basic_info.name. sort by metadata.date_added desc. Paginate with cursor ONLY if needed and keep filters identical across pages. Hard cap pages.
 4. theme_geo(company_id, theme, window): group_by location.country with the theme filter.
 5. tech_probe(company_id, tech_names): filter technographics.technologies.name in [...] (case-sensitive; discover exact values with /job/search/autocomplete first). Remember: matches only postings from professional_network source and is filter-only (never request it in fields).
 6. peer_theme_share(company_ids, theme, window): theme_count / total_count per peer for contrast.
 7. emerging_terms(company_id): pull titles (group_by job_details.title) for recent vs prior windows; compute title-bigram frequency ratio in Python; return top 10 terms that are new/rising. This gives DATA-DRIVEN theme suggestions that do not depend on LLM priors.
Every function returns (result, probe_record) and writes a `probes` row + event. Dedupe rows by crustdata_job_id; also compute dup_key = normalised_title + location.city; compute desc_hash = sha1(first 300 chars of description if present).
Tests with respx fixtures: query builder produces exactly valid bodies (assert no "column", no ">=" / "<="), pagination stops on null cursor, dedupe works, BudgetExceeded stops the loop.
Done when: on the demo company, baseline + one theme probe produce stored derived_metric and hiring signals.
````

## A6 — People + web evidence collectors (P1, feature-flagged)

````
Implement providers/crustdata/people.py and web.py behind flags ENABLE_PEOPLE and ENABLE_WEB (default on, but pipeline must work if both off).
People: search current employees of the resolved company with seniority/title keywords from the theme (use field paths confirmed in docs/crustdata_findings.md; read https://docs.crustdata.com/person-docs/search/reference). ALWAYS pass `fields`, limit <= 10 (cost is per result). Create leadership_change/workforce_movement signals only from fields actually returned (name, title, current employer, start date if present). If no start date -> observed_at null and summary says "start date not available". Public professional data only; do not store contact details or anything beyond name, title, employer, start date, public profile URL.
Web: wrapper for Crustdata Web Search (+ Web Fetch of at most 2 URLs) using the endpoint/body confirmed in findings.md. Queries are generated by templates (company + theme + "launch|partnership|announces"). Create web_evidence signals with title, URL, snippet, date if available. Classify source_class: primary (company domain, official blog/press page) | secondary (news) | other; classification is Python domain-matching, not LLM.
Both must fail soft: on any error/permission denial -> emit event level=warn, return empty, continue.
Tests: failure of web does not break pipeline; primary vs secondary classification.
Done when: both can be toggled off and the pipeline still completes.
````

## A7 — Normaliser + signal features

````
Implement pipeline/normalise.py and pipeline/features.py.
Normaliser: convert provider records into Signal rows. For job rows create ONE hiring signal per deduped posting with: title = job title, summary = a deterministic template ("Posting '<title>' in <location> (category: <cat>), added <date> on <source>") — NO LLM text here, observed_at = metadata.date_added, source_url = job_details.url, source_name = job_details.source, theme_tags from keyword hits (Python), features {seniority_level, reposted, dup_key, desc_hash}. Seniority via regex tiers: intern/junior < mid < senior < lead/staff/principal < manager/director/head/vp/chief.
Derived signals (derived=true, derived_from=[ids], computation filled): for each theme and window -> theme_postings_recent, theme_postings_prior, velocity_ratio (recent per-day rate / prior per-day rate; None if prior == 0, then label "new vs prior window"), concentration (theme / total in same window), seniority_share, geo_new_countries (countries in recent theme postings absent in prior/older total postings), evergreen_ratio (share reposted or duplicate-title-across->=3-locations or identical desc_hash), peer_share_gap.
All numbers are computed here in Python and stored in signal_features; never by an LLM.
Singletons (a theme signal with 1 supporting posting) get status label "weak".
Tests: velocity math incl. zero-prior case, evergreen ratio, seniority regex, dedupe.
Done when: signals + derived metrics list correctly for the demo company with traceable computation text.
````

## A8 — Scoping stage (LLM)

````
Implement pipeline/scoping.py and llm/client.py + llm/schemas.py.
llm/client.py: Anthropic SDK wrapper. `structured(prompt_file, variables, pydantic_model)` forces ONE tool whose input_schema is the pydantic model JSON schema, validates the result, and does up to 2 repair retries feeding back validation errors; temperature 0 for judge/verifier calls, 0.2 otherwise; logs token usage; model from env LLM_MODEL.
Scoping: use PROMPT B1 from docs/PLAYBOOK.md (copy to llm/prompts/b1_scoping.md). Inputs: company profile, competitor names, user question (nullable), emerging_terms from A5. Output pydantic `ScopingResult{themes: [Theme]}` where Theme = {id, label, rationale, source (user_question|data_driven), title_keywords[], description_phrases[], anti_keywords[], tech_keywords[], role_families[]}.
Rules enforced in Python after the LLM: max 5 themes; at least 1 data_driven theme if emerging_terms is non-empty (anti-confirmation bias); description_phrases must be multi-word or specific tokens (reject generic words like "ai", "data"); persist to `themes`.
Tests with a mocked LLM: invalid output triggers repair; generic phrase rejected; data_driven theme injected.
Done when: demo question "Are they moving into robotics?" yields a robotics theme with anti_keywords (e.g. robotic process automation) plus >=1 data-driven theme.
````

## A9 — Probe planner loop (bounded agent)

````
Implement pipeline/planner.py: the ONLY free-form agentic loop in the system.
Use Anthropic tool use with these tools (each maps to a Python executor from A5/A6): job_theme_count, job_theme_rows, job_geo, job_tech, peer_theme_share, people_leadership, web_evidence, stop. Tool inputs are validated pydantic models; theme_id must exist.
Loop: system prompt = PROMPT B2; each turn the user message contains the CURRENT LEDGER SUMMARY (counts only, not raw rows): per theme -> recent/prior counts, rows fetched?, geo done?, tech done?, peers done?, people done?, web done?; remaining budget (calls, credits); steps used.
Guards (Python, non-negotiable): MAX_PROBES (default 25); reject duplicate (tool, params) calls; reject theme_rows before theme_count; reject calls over budget; if the LLM errors twice -> fall back to a DETERMINISTIC default plan: for each theme run count(recent), count(prior), rows(recent) if count>=2, geo, peers; then web_evidence for top theme; then stop.
Planner never sees API keys and never writes signals; executors do. Emit an event per decision with the planner's one-line reason.
Tests: mocked LLM tries duplicate probe -> rejected; budget stops loop; fallback plan runs when LLM fails.
Done when: a full probe loop runs end-to-end on the demo company within budget and the events show each probe + reason.
````

## A10 — Clusterer

````
Implement pipeline/cluster.py using PROMPT B3.
Step 1 (Python): candidate clusters = group hiring/people/web signals by (theme_id x role_family) using theme_tags; attach derived metric signals for that theme.
Step 2 (LLM): send COMPACT rows (id, type, title<=80 chars, date, category, country, seniority, source, tags) — not full descriptions — and get back clusters: {cluster_id, label, theme_id, signal_ids, why_related (<=2 sentences citing ids), noise_signal_ids}.
Step 3 (Python validator): all ids exist; each signal in <=1 primary cluster; a "cluster" needs >=3 distinct postings OR >=2 distinct signal types; otherwise label `weak` (kept, shown as weak, never promoted to a strategic hypothesis). Always keep derived metric signals attached to the cluster they describe.
Persist to clusters + cluster_signals. If zero qualifying clusters -> emit `insufficient_evidence` outcome (this is a valid, honest result and must flow to the report).
Tests: invalid ids dropped, singleton marked weak, insufficient_evidence path.
Done when: demo company yields >=1 cluster, or a clean insufficient_evidence result.
````

## A11 — Hypothesis engine (ACH) — SABSE IMPORTANT REASONING PROMPT

````
Implement pipeline/hypothesize.py using PROMPT B4 (copy to llm/prompts/b4_hypothesis.md) and models from A1.
For each qualifying cluster send: theme definition, cluster signals (compact), derived feature signals (velocity, concentration, seniority_share, geo_new_countries, evergreen_ratio, peer_share_gap) WITH their ids, the user question, and the allowed hypothesis_type enum. LLM returns 1-2 hypotheses per cluster, each with: hypothesis_type, title, statement, supporting_signal_ids, contradicting_signal_ids, alternatives (>=2: one benign, one noise/evergreen), evidence_gaps, recommended_next_indicators (concrete publicly observable things), and a `matrix` of {signal_id, hypothesis_ref(H1|H2|H3), relation(consistent|inconsistent|neutral)} covering every cited signal for all three hypotheses.
Python validators (reject + repair up to 2x, then fall back to a templated 'insufficient evidence' hypothesis):
 - every id exists and belongs to the cluster/investigation
 - supporting >= 2 distinct signals unless status weak
 - banned certainty phrases absent (list in CLAUDE.md) unless a primary-source web_evidence signal is among supporting ids
 - NUMBER GROUNDING: every number/percent/date in statement/alternatives must appear in the provided signal/feature data (regex extract -> set membership); otherwise reject
 - statement must begin with a safe-wording phrase ("Signals suggest", "May indicate", "Worth investigating")
 - hypothesis_type must be from the enum; include contraction_or_cost_cutting as a considered alternative when postings are falling
Persist hypotheses, hypothesis_signals (supports/contradicts), hypothesis_matrix.
Tests: ungrounded number rejected, fake signal id rejected, banned phrase rejected, repair loop works, fallback after 2 failures.
Done when: demo run prints hypotheses each linking to real signal ids with 3-way matrix.
````

## A12 — Falsification engine (counter-evidence)

````
Implement pipeline/falsify.py. Two parts.
(1) TEST REGISTRY in Python — each test is a deterministic function (hypothesis, signals, features) -> TestResult{test_id, outcome: supports|contradicts|neutral|inconclusive, evidence_signal_ids, explanation_template_values}:
 - novelty_test: theme share in `older` window vs `recent` (if similar -> contradicts "emerging")
 - evergreen_test: evergreen_ratio > EVERGREEN_MAX (default 0.4) -> contradicts
 - scale_test: concentration < MIN_CONCENTRATION (default 0.02) or fewer than 3 postings -> contradicts/inconclusive
 - peer_test: peer_share_gap small (peers hire the same) -> contradicts company-specific claim (industry trend)
 - seniority_test: no senior/lead roles -> neutral (weakens capability_buildout)
 - geo_test: for new_geography hypotheses require geo_new_countries non-empty
 - corroboration_test: any primary-source web_evidence signal for theme -> supports (and enables corroborated status)
 - source_diversity_test: number of distinct job_details.source platforms among supporting signals
(2) LLM only SELECTS tests and params via PROMPT B5 (output: list of {test_id, params, reason}); unknown test ids are dropped. It may also request up to 2 extra web_evidence queries aimed at DISCONFIRMING the hypothesis; execute via the existing web executor if budget allows.
Each executed test becomes a stored `tests` row AND, when it yields evidence, a signal (type derived_metric or web_evidence) linked to the hypothesis as contradicts/supports. Contradictions must be shown, never silently reconciled.
Tests: each test function with synthetic features; unknown test id dropped.
Done when: every demo hypothesis shows at least the novelty, evergreen, scale and peer test outcomes.
````

## A13 — Scorer (Python only)

````
Implement pipeline/score.py and scoring_config.py (all weights/thresholds as constants, unit-tested).
Inputs: hypothesis, supporting signals, test results. Output: label Low|Medium|High, score_breakdown (each rule, points, reason), caps_applied.
Default rubric (make constants):
 + distinct supporting postings/signals: >=3 -> +2, ==2 -> +1
 + signal type diversity: >=2 types -> +2
 + independent source platforms (distinct job_details.source or web primary): >=2 -> +1
 + recency: median supporting signal age <= 45d -> +1
 + velocity: ratio >=2 AND recent count >=5 -> +2
 + seniority: any lead/staff/principal/director+ -> +1
 + primary-source corroboration (web_evidence class=primary) -> +2
 - each contradicting test outcome: -2 (novelty, evergreen, peer), -1 (scale, seniority)
Mapping: >=7 High, 4-6 Medium, else Low. CAPS: no High if only one signal type; no High without recent window evidence; if an alternative hypothesis is consistent with ALL supporting signals (from matrix) cap at Medium; any conflicting status -> shown as Conflicting with both sides.
Never output percentages. Evidence status: corroborated only if >=2 distinct underlying sources; else single_source; the LLM statement is always status `hypothesis`.
Tests: table-driven cases including caps.
Done when: every hypothesis has a label + breakdown, and changing one test outcome changes the label deterministically.
````

## A14 — Claim ledger + verifier

````
Implement pipeline/verify.py and the claims/claim_signals tables.
Every sentence that will appear in the brief becomes a Claim {id, text, claim_type: fact|hypothesis|recommendation, signal_ids, status}. FACT claims must cite >=1 signal id; if none -> status unverified and rendered as "Not found / unverified" (never as fact).
Verification pass (PROMPT B6, separate LLM call, temperature 0): for each claim + its cited signals' stored text return {verdict: supported|partial|unsupported|contradicted, reason<=20 words}. Python: unsupported/contradicted facts are removed or re-labelled unverified; partial -> softened wording flag. Compute evidence_coverage = share of FACT claims with supporting source (target >=80%) and expose it in the report header.
Also run deterministic checks: banned phrases, numbers grounded, dates grounded.
Tests: unsupported fact is downgraded; coverage calculation; hypothesis claims never marked as fact.
Done when: report data shows coverage % and per-claim status.
````

## A15 — Brief generation + export

````
Implement pipeline/brief.py using PROMPT B7 and the report structure in PRD Appendix A.
The writer LLM receives ONLY the claim ledger (ids + text + type + status) and returns sections referencing claim ids; it may not add facts. Sections: scope (company, question, timestamp, provider_mode, sources), executive summary (<=120 words), observed signals (timeline), hypotheses (strength, supporting, contradicting, alternatives, gaps), unknowns, recommended next indicators, source register (provider/source, URL or reference, retrieval date).
Renderer: Jinja2 -> HTML with three visually distinct blocks: FACT (neutral/blue), HYPOTHESIS (amber, always labelled), RECOMMENDATION (green). Every hypothesis expands to its exact signals + source links. If provider_mode != live show a banner "Contains replayed/seeded data". Export: HTML with print CSS, and PDF via weasyprint/playwright; if PDF fails, serve printable HTML.
Done when: GET /api/investigations/{id}/export returns a downloadable file and clicking a hypothesis in the HTML shows source links.
````

## A16 — Runner + FastAPI + live events

````
Implement pipeline/runner.py (stage sequence with checkpointing so POST /run can resume) and main.py routes exactly as PRD 8.3. Add GET /api/investigations/{id}/events as SSE streaming rows from the `events` table (real events only). Run the pipeline in a background task. On any stage failure: record the error, mark the stage failed, and continue with the best available data (graceful degradation) — final status `completed_with_warnings`. If company resolution is ambiguous: status `needs_confirmation` and wait for POST /api/investigations/{id}/confirm_company.
Add scripts/run_investigation.py for CLI runs and print a stage-by-stage summary with credits used.
Done when: the CLI completes an end-to-end run and the API returns signals, hypotheses and report.
````

## A17 — Frontend (React, minimal)

````
Build the 5 PRD screens (section 10) in frontend/ with Vite+React. Priority: (1) live investigation timeline reading the SSE stream (show current tool call + success/failure; no fake progress), (2) signal timeline cards, (3) hypothesis cards with FACT / HYPOTHESIS / RECOMMENDATION visual separation and a click-through drawer listing exact supporting AND contradicting signals with source links and retrieval dates, (4) company confirmation dialog for ambiguous matches, (5) brief view + export button. Show evidence strength as Low/Medium/High only with an expandable score_breakdown. Show provider_mode badge. Keep styling simple and clean.
If time is critical, say so and build the same screens in Streamlit instead.
Done when: demo flow from PRD section 12 works in the browser.
````

## A18 — Fallback + chaos tests

````
Implement PROVIDER_MODE=replay using fixtures recorded by scripts/discover.py and a real run (RECORD=1). Replay data must be real captured responses, shown with a visible "REPLAY" badge — never fabricated. Add a failure-injection switch (env FAIL_ENDPOINT=/job/search etc.) and pytest cases proving: job API down -> report still produced with warning + explicit unknowns; web down -> hypotheses produced without corroboration; LLM down -> deterministic templated report (facts + features, no hypotheses, clearly stated); budget exceeded -> partial but valid report.
Done when: the 5 PRD resilience scenarios pass in `make test`.
````

## A19 — Demo-company screener + rehearsal + README

````
Write scripts/screen_candidates.py: given 3-5 candidate company domains, run only baseline + theme_count for the demo theme and rank by signal richness (recent theme postings, distinct sources, seniority, velocity). Output a table so I pick the demo company from REAL data; do not hard-code any theme discovery.
Write scripts/rehearse.py running the full 3-minute demo path twice and timing each stage.
Write README.md per PRD section 15: problem, solution, setup, architecture diagram (mermaid) + data flow, API dependencies, limitations (stale/closed postings, provider-normalised data, hypotheses are not predictions), seeded-data disclosure, credits used.
Done when: README renders, rehearse passes twice.
````

---

# PART B — Runtime LLM prompts (agent ke andar use hone wale)

`backend/app/llm/prompts/` mein save karo. `{{...}}` variables hain.

## B1 — Scoping

````
You are the scoping module of a competitive-intelligence analyst.

INPUT
Company profile: {{company_profile}}
Competitor set: {{competitors}}
User question (may be empty): {{user_question}}
Data-driven emerging title terms (computed by code from recent vs prior job postings): {{emerging_terms}}

TASK
Define up to 5 investigation THEMES to test against public job-posting and people data.
- If the user asked a question, create 1-3 themes that directly test it (source="user_question").
- ALWAYS also create 1-2 themes from the emerging terms (source="data_driven"), even if they seem unrelated to the question. Do not let the question bias you into ignoring what the data shows.
- If the question is empty, derive all themes from the emerging terms.

For each theme output: id (t1..t5), label, rationale (1 sentence, no factual claims about the company), title_keywords (3-8 job-title words/phrases), description_phrases (3-8 SPECIFIC phrases for exact-phrase matching in job descriptions; multi-word or distinctive tokens; never generic words like "ai", "data", "innovation"), anti_keywords (phrases that look similar but mean something else, e.g. "robotic process automation" for a robotics theme), tech_keywords (exact product/technology names, case as normally written), role_families (e.g. engineering, sales, operations, research).

RULES
- Do not assert anything about what the company is doing. You are choosing what to TEST.
- Use only the provided inputs for facts about the company.
- Return via the provided tool/schema only.
````

## B2 — Probe planner

````
You are the investigation planner for SignalSleuth. You decide which data probe to run next. You do not analyse or conclude.

TOOLS: job_theme_count, job_theme_rows, job_geo, job_tech, peer_theme_share, people_leadership, web_evidence, stop.

CURRENT LEDGER (counts only): {{ledger_summary}}
BUDGET LEFT: {{calls_left}} calls, {{credits_left}} credits. Steps used: {{steps_used}}/{{max_steps}}.

POLICY
1. Always prefer cheap probes first: counts before rows. Never call job_theme_rows for a theme unless its recent count is >= 2.
2. Spend budget where evidence is promising: a theme with recent postings > 0 deserves rows, geo, peers. A theme with zero recent AND zero prior postings should be dropped (do not probe it further).
3. Use peer_theme_share only for themes with at least 3 recent postings (it is a contrast test).
4. Use people_leadership and web_evidence only for the top 1-2 themes by recent count, and only after job probes.
5. Never repeat an identical call. Never invent theme ids.
6. Call stop when every promising theme has: counts (recent+prior), rows, geo, peers; or when budget is nearly exhausted; or when no theme has signal.
Each tool call must include a one-line "reason" (<=20 words) referencing ledger numbers.
Output tool calls only.
````

## B3 — Cluster

````
You group related public signals about ONE company into clusters. Signals are facts; you must not add facts.

THEME: {{theme}}
SIGNALS (id | type | title | date | category | country | seniority | source | tags):
{{signals_compact}}

TASK
Return clusters of signal ids that plausibly relate to the same underlying activity. For each cluster: cluster_id, label (<=8 words, descriptive not conclusive), theme_id, signal_ids, why_related (<=2 sentences citing ids and only information visible in the rows), noise_signal_ids (signals in the candidate set that do not belong).
RULES
- Use only ids provided. Each signal in at most one cluster.
- Do not infer motives or strategy here. Label by WHAT the signals share (role family, technology, geography), not WHY.
- A cluster of one signal is not a cluster: put it in noise_signal_ids.
- Derived metric signals (type derived_metric) describe a cluster; attach them to the cluster they computed for.
````

## B4 — Hypothesis (competing hypotheses)

````
You are a careful competitive-intelligence analyst. You form HYPOTHESES from public signals and you test them against rival explanations. You never state conclusions as facts.

USER QUESTION: {{user_question}}
THEME: {{theme}}
CLUSTER SIGNALS (id | type | title | date | source | tags): {{cluster_signals}}
COMPUTED FEATURES (from code; use verbatim, do not recompute): {{features_with_ids}}
PEER CONTRAST (from code): {{peer_features}}
ALLOWED hypothesis_type: new_product_category | new_geography | new_customer_segment | capability_buildout | go_to_market_shift | leadership_or_org_change | contraction_or_cost_cutting | insufficient_evidence

TASK
Produce 1-2 hypotheses for this cluster. For EACH hypothesis H1 also write two rivals:
- H2: a benign or alternative explanation (e.g. supports an existing product, backfill, industry-wide trend).
- H3: a noise explanation (e.g. evergreen/duplicate postings, aggregator reposts, one-off hiring).
Then fill the matrix: for EVERY cited signal id x {H1,H2,H3} label consistent | inconsistent | neutral. A signal consistent with all three is not diagnostic; say so in diagnostic_note.

OUTPUT FIELDS per hypothesis: hypothesis_type, title, statement (1-2 sentences, must start with "Signals suggest", "May indicate" or "Worth investigating"), supporting_signal_ids, contradicting_signal_ids, alternatives [{statement, would_be_supported_by, consistent_signal_ids}], evidence_gaps, recommended_next_indicators (concrete, publicly observable: product pages, specific role types, partner announcements, new office postings), matrix, diagnostic_note (<=2 sentences).

HARD RULES
1. Cite only signal ids given. Every claim in statement must be traceable to cited ids.
2. Use NO number, percentage or date that is not present in the input. Do not compute anything; copy feature values exactly.
3. Never use: will launch, is launching, definitely, confirmed, plans to, is building. Hiring is not a plan.
4. Do not infer private motives, confidential plans, or anything about individuals beyond their public job title.
5. If the cluster is thin, return hypothesis_type = insufficient_evidence and explain the gaps instead of stretching.
6. Contraction (falling or flat postings) is a valid hypothesis; consider it when velocity is low or negative.
7. Do not give any confidence score or percentage. Strength is assigned by code.
````

## B5 — Falsifier test selection

````
You select tests that could DISPROVE a hypothesis. You do not run them.

HYPOTHESIS: {{hypothesis}}
AVAILABLE TESTS (ids and what they check): novelty_test, evergreen_test, scale_test, peer_test, seniority_test, geo_test, corroboration_test, source_diversity_test
SIGNAL COVERAGE SO FAR: {{coverage}}

Return a list of {test_id, params, reason}. Choose every test relevant to this hypothesis_type (e.g. geo_test only for new_geography). Always include novelty_test, evergreen_test, scale_test and peer_test. You may add up to 2 web_evidence queries designed to find DISCONFIRMING public evidence (e.g. company statements that the roles support an existing product). Use only listed test ids. No analysis text.
````

## B6 — Claim verifier (LLM judge, temperature 0)

````
You verify whether a claim is supported by the cited evidence. Be strict.

CLAIM: {{claim_text}}
CLAIM TYPE: {{claim_type}}
CITED EVIDENCE (stored text of each signal, with id): {{evidence}}

Return {verdict, reason}. verdict:
- supported: every specific element of the claim (entities, numbers, dates, direction) is stated in the evidence
- partial: the evidence supports part of the claim or a weaker version
- unsupported: the evidence does not state the claim
- contradicted: the evidence conflicts with the claim
For claim_type = hypothesis, judge only whether the cited evidence is consistent with the stated reasoning and that the wording does not overstate (e.g. "will", "is building"). reason <= 20 words. Use only the evidence shown; ignore anything you may know about the company.
````

## B7 — Brief writer

````
You write the intelligence brief from a verified claim ledger. You may NOT add facts, numbers, dates, or sources that are not in the ledger.

LEDGER (claim_id | type | status | text | signal_ids): {{claims}}
SCOPE: {{scope}}
EVIDENCE COVERAGE: {{coverage}}

Write the sections: executive_summary (<=120 words), key_findings, hypotheses, counter_evidence_and_alternatives, unknowns, recommended_next_indicators. Every sentence must carry the claim_ids it relies on. Keep FACT, HYPOTHESIS and RECOMMENDATION separate. Use safe wording ("signals suggest", "may indicate", "worth investigating"). Say "postings", not "openings". Claims with status unverified must appear only in the unknowns section, labelled "not verified". If the outcome is insufficient evidence, say so plainly and list what would change that.
````

---

# PART C — Demo-day checklist (data pipeline specific)

- [ ] `docs/crustdata_findings.md` committed (proof you checked the real API)
- [ ] Demo company picked via `screen_candidates.py` on real data, backup company recorded in fixtures
- [ ] Planner events visible in UI (real tool calls, reasons)
- [ ] One hypothesis shows a **contradicting** signal or a failed test (this proves it isn't confirmation bias)
- [ ] Number-grounding + banned-phrase tests green
- [ ] Failure injection demo: kill `/job/search` -> labelled fallback report
- [ ] Credits used printed in README
- [ ] Wording check: "postings", "signals suggest", no "will launch"

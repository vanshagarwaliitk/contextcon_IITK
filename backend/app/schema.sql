-- CoreData schema: PRD section 9 tables + PLAYBOOK 0.7 extras.

CREATE TABLE IF NOT EXISTS investigations (
    id TEXT PRIMARY KEY,
    company_name TEXT,
    domain TEXT,
    resolved_company_id INTEGER,
    resolved_name TEXT,
    resolved_domain TEXT,
    user_question TEXT,
    competitors_json TEXT DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'created',
    provider_mode TEXT NOT NULL DEFAULT 'live',
    stage_state_json TEXT DEFAULT '{}',
    profile_json TEXT,
    candidates_json TEXT,
    report_json TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS signals (
    id TEXT NOT NULL,
    investigation_id TEXT NOT NULL,
    signal_type TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    observed_at TEXT,
    source_name TEXT,
    source_url TEXT,
    provider TEXT,
    endpoint TEXT,
    raw_ref TEXT,
    retrieved_at TEXT,
    provider_mode TEXT,
    derived INTEGER DEFAULT 0,
    derived_from_json TEXT DEFAULT '[]',
    computation TEXT,
    theme_tags_json TEXT DEFAULT '[]',
    features_json TEXT DEFAULT '{}',
    status TEXT DEFAULT 'single_source',
    PRIMARY KEY (investigation_id, id)
);

CREATE TABLE IF NOT EXISTS signal_features (
    investigation_id TEXT NOT NULL,
    theme_id TEXT NOT NULL,
    name TEXT NOT NULL,
    value_json TEXT,
    signal_id TEXT,
    PRIMARY KEY (investigation_id, theme_id, name)
);

CREATE TABLE IF NOT EXISTS themes (
    investigation_id TEXT NOT NULL,
    id TEXT NOT NULL,
    label TEXT NOT NULL,
    source TEXT NOT NULL,
    definition_json TEXT NOT NULL,
    PRIMARY KEY (investigation_id, id)
);

CREATE TABLE IF NOT EXISTS probes (
    id TEXT NOT NULL,
    investigation_id TEXT NOT NULL,
    tool TEXT NOT NULL,
    params_json TEXT NOT NULL,
    status TEXT NOT NULL,
    reason TEXT,
    rows INTEGER DEFAULT 0,
    credits_est REAL DEFAULT 0,
    result_json TEXT,
    signal_ids_json TEXT DEFAULT '[]',
    created_at TEXT NOT NULL,
    PRIMARY KEY (investigation_id, id)
);

CREATE TABLE IF NOT EXISTS clusters (
    id TEXT NOT NULL,
    investigation_id TEXT NOT NULL,
    theme_id TEXT,
    label TEXT,
    why_related TEXT,
    status TEXT NOT NULL,        -- qualifying | weak
    PRIMARY KEY (investigation_id, id)
);

CREATE TABLE IF NOT EXISTS cluster_signals (
    investigation_id TEXT NOT NULL,
    cluster_id TEXT NOT NULL,
    signal_id TEXT NOT NULL,
    PRIMARY KEY (investigation_id, cluster_id, signal_id)
);

CREATE TABLE IF NOT EXISTS hypotheses (
    id TEXT NOT NULL,
    investigation_id TEXT NOT NULL,
    cluster_id TEXT,
    theme_id TEXT,
    hypothesis_type TEXT NOT NULL,
    title TEXT NOT NULL,
    explanation TEXT NOT NULL,
    evidence_strength TEXT,          -- Low | Medium | High
    status TEXT NOT NULL DEFAULT 'hypothesis',
    evidence_status TEXT,            -- corroborated | single_source | conflicting | unverified
    alternatives_json TEXT DEFAULT '[]',
    evidence_gaps_json TEXT DEFAULT '[]',
    next_indicators_json TEXT DEFAULT '[]',
    diagnostic_note TEXT,
    score_breakdown_json TEXT DEFAULT '{}',
    PRIMARY KEY (investigation_id, id)
);

CREATE TABLE IF NOT EXISTS hypothesis_signals (
    investigation_id TEXT NOT NULL,
    hypothesis_id TEXT NOT NULL,
    signal_id TEXT NOT NULL,
    relationship TEXT NOT NULL,      -- supports | contradicts
    PRIMARY KEY (investigation_id, hypothesis_id, signal_id, relationship)
);

CREATE TABLE IF NOT EXISTS hypothesis_matrix (
    investigation_id TEXT NOT NULL,
    hypothesis_id TEXT NOT NULL,
    hypothesis_ref TEXT NOT NULL,    -- H1 | H2 | H3
    signal_id TEXT NOT NULL,
    relation TEXT NOT NULL,          -- consistent | inconsistent | neutral
    PRIMARY KEY (investigation_id, hypothesis_id, hypothesis_ref, signal_id)
);

CREATE TABLE IF NOT EXISTS tests (
    id TEXT NOT NULL,
    investigation_id TEXT NOT NULL,
    hypothesis_id TEXT NOT NULL,
    test_id TEXT NOT NULL,
    outcome TEXT NOT NULL,
    explanation TEXT,
    evidence_signal_ids_json TEXT DEFAULT '[]',
    reason TEXT,
    PRIMARY KEY (investigation_id, id)
);

CREATE TABLE IF NOT EXISTS claims (
    id TEXT NOT NULL,
    investigation_id TEXT NOT NULL,
    text TEXT NOT NULL,
    claim_type TEXT NOT NULL,        -- fact | hypothesis | recommendation
    status TEXT NOT NULL,
    verdict TEXT,
    verdict_reason TEXT,
    section TEXT,
    PRIMARY KEY (investigation_id, id)
);

CREATE TABLE IF NOT EXISTS claim_signals (
    investigation_id TEXT NOT NULL,
    claim_id TEXT NOT NULL,
    signal_id TEXT NOT NULL,
    PRIMARY KEY (investigation_id, claim_id, signal_id)
);

CREATE TABLE IF NOT EXISTS api_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    investigation_id TEXT,
    provider TEXT,
    endpoint TEXT,
    label TEXT,
    status_code INTEGER,
    credits_estimate REAL,
    error TEXT,
    latency_ms INTEGER,
    cache_hit INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    investigation_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    level TEXT NOT NULL,             -- info | warn | error
    message TEXT NOT NULL,
    payload_json TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS id_counters (
    investigation_id TEXT NOT NULL,
    prefix TEXT NOT NULL,
    value INTEGER NOT NULL,
    PRIMARY KEY (investigation_id, prefix)
);

CREATE TABLE IF NOT EXISTS watchlist (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    investigation_id TEXT NOT NULL,
    item_type TEXT NOT NULL,         -- hypothesis | signal
    item_id TEXT NOT NULL,
    note TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_inv ON events (investigation_id, id);
CREATE INDEX IF NOT EXISTS idx_api_logs_inv ON api_logs (investigation_id);

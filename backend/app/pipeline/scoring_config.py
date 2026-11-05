"""All scoring weights / thresholds (PLAYBOOK A13). Change here, tests cover them."""

DISTINCT_SIGNALS_3_PLUS = 2
DISTINCT_SIGNALS_2 = 1
TYPE_DIVERSITY_MIN = 2
TYPE_DIVERSITY_POINTS = 2
SOURCE_PLATFORMS_MIN = 2
SOURCE_PLATFORMS_POINTS = 1
RECENCY_MAX_MEDIAN_DAYS = 45
RECENCY_POINTS = 1
VELOCITY_MIN_RATIO = 2.0
VELOCITY_MIN_RECENT = 5
VELOCITY_POINTS = 2
SENIORITY_MIN_TIER = 3          # lead/staff/principal and above
SENIORITY_POINTS = 1
PRIMARY_CORROBORATION_POINTS = 2

CONTRADICTION_PENALTY = {
    "novelty_test": -2,
    "evergreen_test": -2,
    "peer_test": -2,
    "geo_test": -2,
    "scale_test": -1,
    "seniority_test": -1,
    "gtm_test": -1,
    "scale_phase_test": -1,
}

HIGH_MIN = 7
MEDIUM_MIN = 4
RECENT_WINDOW_DAYS = 30

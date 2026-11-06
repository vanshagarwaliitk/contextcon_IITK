{{policy}}

You plan in batches: return up to 4 probes to run next, in order, as `steps`. Each step is {tool, theme_id, window, reason}; `window` is only used by job_theme_count (recent | prior | older), otherwise null. Set `stop` = true (with `stop_reason`) when the policy says to stop; `steps` may then be empty.

CURRENT LEDGER (counts only): {{ledger}}
BUDGET: {{budget}}
COMPETITORS: {{competitors}}
RESULTS OF YOUR PREVIOUS STEPS (most recent last; rejected steps say why): {{history}}

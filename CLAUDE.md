# CoreData — notes for Claude Code

Specs: `coredata/docs/sytem_impl.docx` (PRD) and `coredata/docs/PLAYBOOK.md` (architecture + build prompts A0–A19, runtime prompts B1–B7).

## Layout
- `backend/app/providers/crustdata/` — every Crustdata call goes through `client.py` (budget, cache, record/replay, retries, api_logs).
- `backend/app/pipeline/` — staged deterministic pipeline; `runner.py` lists the stages in order.
- `backend/app/llm/` — Claude wrapper (`client.py`), output schemas, prompts `prompts/b1..b7`.
- `backend/tests/` — `conftest.py` has a fake Crustdata server + `ScriptedLLM`.

## Rules (do not break)
- The LLM is used in exactly 7 bounded places (scoping, planner, cluster, hypothesis, test selection, verifier, brief writer). All numbers, scores and evidence labels are computed in Python.
- Planner is the only agentic loop; its guards live in `pipeline/planner.py::Planner.execute`.
- Every LLM output is validated in Python; failures repair up to 2x, then fall back deterministically.
- Banned phrases (see `pipeline/guards.py::BANNED_PHRASES`): will launch, is launching, definitely, confirmed, plans to, is building, …
- Hypothesis statements start with "Signals suggest" / "May indicate" / "Worth investigating". Say "postings", not "openings".
- Missing data → null / "Not found". Never fill with a model guess. Replay data must be real recorded responses.
- Crustdata filter syntax: `"field"` (not column), `=>` / `=<` (not >= / <=), `[.]` exact phrase, `(.)` all words.
- Model: `claude-opus-5-5` (no temperature, no forced tool_choice; effort via `output_config`).

## Commands
- Tests: `cd backend && ../.venv/Scripts/python -m pytest -q`
- Server: `.venv/Scripts/python -m uvicorn app.main:app --app-dir backend --port 8000`

PY ?= .venv/Scripts/python

.PHONY: install dev test discover run

install:
	python -m venv .venv && $(PY) -m pip install -r requirements.txt

dev:
	$(PY) -m uvicorn app.main:app --app-dir backend --reload --port 8000

test:
	cd backend && ../$(PY) -m pytest -q

discover:
	$(PY) -m scripts.discover $(DOMAIN) --theme "$(THEME)" --record

run:
	$(PY) -m scripts.run_investigation --domain $(DOMAIN) --question "$(QUESTION)" --competitors "$(COMPETITORS)" --html brief.html

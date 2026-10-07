.PHONY: install run test lint fmt audit check clean

VENV ?= .venv
PY   := $(VENV)/bin/python

install:  ## virtual muhit va bog'liqliklarni o'rnatish
	python3 -m venv $(VENV)
	$(PY) -m pip install -q -r requirements-dev.txt -c constraints.txt

run:  ## lokal server (kod o'zgarsa qayta yuklanadi)
	SCHEDULER=0 $(PY) -m uvicorn app.main:app --reload --port 8000

test:  ## testlar
	$(PY) -m pytest -q

lint:  ## lint va formatlash tekshiruvi (o'zgartirmaydi)
	$(PY) -m ruff check .
	$(PY) -m ruff format --check .

fmt:  ## avtomatik tuzatish va formatlash
	$(PY) -m ruff check . --fix
	$(PY) -m ruff format .

audit:  ## xavfsizlik: kod (bandit) va bog'liqliklar (pip-audit)
	$(PY) -m bandit -q -r app
	$(PY) -m pip_audit -r requirements.txt

check: lint test audit  ## PR'dan oldin hammasini tekshirish (CI shuni bajaradi)

clean:
	rm -rf .pytest_cache .ruff_cache test.db saas.db
	find . -name __pycache__ -type d -prune -exec rm -rf {} +

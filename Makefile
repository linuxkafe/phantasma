.PHONY: setup run test test-mobile lint format build check doctor help venv typecheck check-wakewords

AES_LANGUAGE ?= python
AES_LINT ?= ruff check
AES_TEST ?= pytest
AES_FORMAT ?= ruff format
AES_BUILD ?= python -m build
AES_RUN ?= python -m src.main || python src/main.py
VENV_DIR ?= venv
VENV_PYTHON := $(VENV_DIR)/bin/python
VENV_PIP := $(VENV_DIR)/bin/pip

export AES_LANGUAGE AES_LINT AES_TEST AES_FORMAT AES_BUILD AES_RUN

venv:
	@test -d $(VENV_DIR) || python3 -m venv $(VENV_DIR)
	@$(VENV_PIP) install --upgrade pip

setup: venv
	@echo "Setting up $(AES_LANGUAGE) in $(VENV_DIR)..."
	@$(VENV_PIP) install --extra-index-url https://download.pytorch.org/whl/cpu
	@echo "    installing dev tools (playwright drives the layout tests)"
	@$(VENV_PIP) install --quiet playwright ruff -e .[dev]

run: venv
	@$(VENV_PYTHON) -m src.main 2>/dev/null || $(VENV_PYTHON) src/main.py

# Verify PT wake word models exist and load in the image BEFORE compose up (T034)
check-wakewords:
	@scripts/check-wakewords.sh check

test: venv
	@$(VENV_PYTHON) -m pytest

test-mobile: venv
	@$(VENV_PYTHON) -m pytest tests/test_mobile_*.py -v

lint: venv
	@$(VENV_PYTHON) -m ruff check src tests assistant.py config.py src/main.py

format: venv
	@$(VENV_PYTHON) -m ruff format src tests assistant.py config.py src/main.py

typecheck: venv
	@$(VENV_PYTHON) -m mypy src tests assistant.py config.py --ignore-missing-imports --exclude src/main.py

build: venv
	@$(VENV_PYTHON) -m build

check: docs-check code-check test-check lint-check

docs-check:
	@test -f docs/VISION.md && grep -q "Problem" docs/VISION.md
	@test -f docs/PERSONAS.md && grep -q "User" docs/PERSONAS.md
	@test -f docs/REQUIREMENTS.md && grep -q "Functional" docs/REQUIREMENTS.md
	@test -f docs/ROADMAP.md && grep -q "Roadmap" docs/ROADMAP.md

code-check:
	@test -d src || test -d lib
	@grep -R "TODO:" src/ tests/ 2>/dev/null || true

test-check: venv
	@$(VENV_PYTHON) -m pytest --cov=src --cov-fail-under=30 || echo "Coverage below 30%"

lint-check: venv
	@$(VENV_PYTHON) -m ruff check src tests assistant.py config.py src/main.py

typecheck-check: venv
	@$(VENV_PYTHON) -m mypy src tests assistant.py config.py src/main.py --ignore-missing-imports

validate: venv
	@$(VENV_PYTHON) -m ruff check . || true

doctor:
	@echo "Language: $(AES_LANGUAGE)"
	@echo "Python: $$($(VENV_PYTHON) --version 2>&1 || echo not-found)"
	@echo "Venv: $(VENV_DIR)"

help:
	@echo "AES Commands: make setup run test lint format build check doctor typecheck"

## Deploy pHantasma to production (dev -> prod, one direction only)
deploy:
	@scripts/deploy.sh --dry-run
deploy-apply:
	@scripts/deploy.sh
deploy-status:
	@scripts/deploy.sh --dry-run

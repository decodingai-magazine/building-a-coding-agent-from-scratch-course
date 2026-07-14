# Standalone single-package project — every recipe wraps `uv`.
.DEFAULT_GOAL := help

install:  ## Install/refresh the venv from pyproject + uv.lock, and wire git hooks.
	uv sync
	uv run pre-commit install --hook-type pre-commit --hook-type pre-push

test:  ## Run the full test suite (unit + integration).
	uv run pytest

unit-tests:  ## Run unit tests only.
	uv run pytest tests/unit

integration-tests:  ## Run integration tests only.
	uv run pytest tests/integration

lint-check:  ## Lint without writing.
	uv run ruff check

lint-fix:  ## Lint and auto-fix.
	uv run ruff check --fix

format-check:  ## Check formatting without writing.
	uv run ruff format --check

format-fix:  ## Format the code.
	uv run ruff format

pre-commit:  ## Fast gate: format-check + lint-check + unit tests.
	$(MAKE) format-check
	$(MAKE) lint-check
	$(MAKE) unit-tests

eval-regression:  ## Pre-merge behavior regression gate (needs GEMINI_API_KEY + OPIK_API_KEY; skips without). Costs money; never in CI.
	uv run pytest evals/regression/test_thresholds.py

sync-secrets:  ## Mirror .env into the Kitaru environment bucket decode-$(ENV). Usage: make sync-secrets ENV=staging
	@[ -n "$(ENV)" ] || { echo "Usage: make sync-secrets ENV=dev|staging|prod   (one-way: .env -> the decode-<ENV> bucket)"; exit 1; }
	uv run python scripts/sync_secrets.py --env $(ENV)

build:  ## Build wheel + sdist into dist/.
	uv build

install-cli:  ## Put `decode` on your PATH (editable: tracks your source). Then just type `decode`.
	uv tool install --editable .
	@echo "Installed. If 'decode' is not found, add uv's tool bin to PATH: run 'uv tool update-shell' then restart your shell."

uninstall-cli:  ## Remove the `decode` command from your PATH.
	uv tool uninstall decode

ci:  ## What CI runs: lockfile check + format-check + lint-check + full tests.
	uv lock --check
	$(MAKE) format-check
	$(MAKE) lint-check
	$(MAKE) test

help:  ## Show this help.
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

.PHONY: install test unit-tests integration-tests lint-check lint-fix format-check format-fix pre-commit eval-regression sync-secrets build install-cli uninstall-cli ci help

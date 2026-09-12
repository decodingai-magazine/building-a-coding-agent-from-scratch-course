# Standalone single-package project — every recipe wraps `uv`.
.DEFAULT_GOAL := help

help:  ## Show this help.
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

install:  ## Install/refresh the venv from pyproject + uv.lock, and wire git hooks.
	uv sync
	uv run pre-commit install --hook-type pre-commit --hook-type pre-push

build:  ## Build wheel + sdist into dist/.
	uv build

install-cli:  ## Put `decode` on your PATH (editable: tracks your source). Then just type `decode`.
	uv tool install --editable .
	@echo "Installed. If 'decode' is not found, add uv's tool bin to PATH: run 'uv tool update-shell' then restart your shell."

uninstall-cli:  ## Remove the `decode` command from your PATH.
	uv tool uninstall decode

##### Evals ######

eval-benchmark:  ## Outcome benchmark as an Opik experiment (needs OPIK_API_KEY + provider key; skips friendly without). Costs money; never in CI. Pass flags via ARGS='--trials 3 --sandbox modal'.
	@if uv run python -m evals.harness.keys; then \
		uv run python -m evals benchmark $(ARGS); \
	fi

eval-regression:  ## Pre-merge behavior regression gate: sync cases + threshold gate (needs OPIK_API_KEY + provider key; skips friendly without). Costs money; never in CI. Slice a tier with ARGS='--difficulty hard'.
	@if uv run python -m evals.harness.keys; then \
		uv run python -m evals sync --no-benchmark --regression $(ARGS) && uv run pytest evals/regression/test_thresholds.py $(ARGS); \
	fi

KITARU_LOCAL_URL ?= http://localhost:8000

kitaru-local:  ## Start the local OSS Kitaru server (docker compose) and register decode on it. `kitaru logout` stops it.
	uv run kitaru login --local
	uv run python scripts/bootstrap_kitaru.py --server $(KITARU_LOCAL_URL)
	@echo ""
	@echo "# Export these two: decode reads KITARU_AGENT_ID, the kitaru adapter reads KITARU_API_URL."
	@echo "export KITARU_API_URL=$(KITARU_LOCAL_URL)"
	@printf 'export KITARU_AGENT_ID=%s\n' "$$(uv run kitaru agent get decode --server $(KITARU_LOCAL_URL) --output json | uv run python -c 'import json,sys; print(json.load(sys.stdin)["item"]["id"])')"

##### Dev ######

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

ci:  ## What CI runs: lockfile check + format-check + lint-check + full tests.
	uv lock --check
	$(MAKE) format-check
	$(MAKE) lint-check
	$(MAKE) test

.PHONY: install test unit-tests integration-tests lint-check lint-fix format-check format-fix pre-commit eval-benchmark eval-regression kitaru-local build install-cli uninstall-cli ci help

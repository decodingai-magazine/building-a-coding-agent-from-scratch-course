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

eval-regression-dataset:  ## Regression Cases, deterministic track: sync cases to the Opik dataset + threshold gate (needs OPIK_API_KEY + provider key; skips friendly without). Costs money; never in CI. Slice a tier with ARGS='--difficulty hard'.
	@if uv run python -m evals.harness.keys; then \
		uv run python -m evals sync --no-benchmark --regression $(ARGS) && uv run pytest evals/regression/test_thresholds.py $(ARGS); \
	fi

eval-regression-suite:  ## Regression Cases, LLM-judged track: the Opik Test Suite over each case's English assertion (needs OPIK_API_KEY + provider key; skips friendly without). Costs money; never in CI. Same ARGS as eval-regression-dataset.
	@if uv run python -m evals.harness.keys; then \
		uv run python -m evals suite $(ARGS); \
	fi

KITARU_LOCAL_URL ?= http://localhost:8000
# The server to register on: `make kitaru-bootstrap KITARU_API_URL=<url>` > the shell env > `.env` > local.
KITARU_API_URL ?= $(or $(shell sed -n 's/^KITARU_API_URL=//p' .env 2>/dev/null | tail -n 1),$(KITARU_LOCAL_URL))

kitaru-local:  ## Start the local OSS Kitaru server (docker compose) and register decode on it. `kitaru logout` stops it.
	uv run kitaru login --local
	$(MAKE) kitaru-bootstrap KITARU_API_URL=$(KITARU_LOCAL_URL)

kitaru-bootstrap:  ## Register decode on KITARU_API_URL (arg > env > .env > local), then print the two .env lines. ARGS=--dry-run changes nothing.
	uv run python scripts/bootstrap_kitaru.py --server $(KITARU_API_URL) $(ARGS)
	@echo ""
	@echo "# Put these two in .env:"
	@echo "KITARU_API_URL=$(KITARU_API_URL)"
	@printf 'KITARU_AGENT_ID=%s\n' "$$(uv run kitaru agent get decode --server $(KITARU_API_URL) --output json 2>/dev/null | uv run python -c 'import json,sys; d=sys.stdin.read().strip(); print(json.loads(d)["item"]["id"] if d else "<no decode agent there yet: check uv run kitaru status>")')"

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

.PHONY: install test unit-tests integration-tests lint-check lint-fix format-check format-fix pre-commit eval-benchmark eval-regression-dataset eval-regression-suite kitaru-local build install-cli uninstall-cli ci help

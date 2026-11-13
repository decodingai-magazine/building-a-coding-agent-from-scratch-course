<#
.SYNOPSIS
    Build and automation script equivalent to Makefile for Windows (PowerShell).
.EXAMPLE
    .\build.ps1 install
    .\build.ps1 test
    .\build.ps1 kitaru-bootstrap -KitaruApiUrl "http://localhost:8000"
#>

[CmdletBinding()]
param (
    [Parameter(Position=0)]
    [ValidateSet(
        "help", "install", "build", "install-cli", "uninstall-cli",
        "eval-benchmark", "eval-regression-dataset", "eval-regression-suite",
        "kitaru-local", "kitaru-bootstrap", "kitaru-seed",
        "test", "unit-tests", "integration-tests",
        "lint-check", "lint-fix", "format-check", "format-fix",
        "pre-commit", "ci"
    )]
    [string]$Target = "help",

    [Parameter(Position=1, ValueFromRemainingArguments=$true)]
    [string[]]$ArgsList,

    [string]$KitaruApiUrl = $env:KITARU_API_URL,
    [string]$KitaruLocalUrl = "http://localhost:8000"
)

# Helper function to read .env values if environment variable is not set
function Get-EnvValue ([string]$key) {
    if (Test-Path ".env") {
        $match = Get-Content ".env" | Select-String -Pattern "^$key=(.*)$"
        if ($match) { return $match.Matches.Groups[1].Value.Trim() }
    }
    return $null
}

switch ($Target) {

    "install" {
        uv sync
        uv run pre-commit install --hook-type pre-commit --hook-type pre-push
    }

    "build" {
        uv build
    }

    "install-cli" {
        uv tool install --editable .
        Write-Host "Installed. If 'decode' is not found, add uv's tool bin to PATH: run 'uv tool update-shell' then restart your shell." -ForegroundColor Cyan
    }

    "uninstall-cli" {
        uv tool uninstall decode
    }

    "eval-benchmark" {
        uv run python -m evals.harness.keys
        if ($LASTEXITCODE -eq 0) {
            uv run python -m evals benchmark @ArgsList
        }
    }

    "eval-regression-dataset" {
        uv run python -m evals.harness.keys
        if ($LASTEXITCODE -eq 0) {
            uv run python -m evals sync --no-benchmark --regression @ArgsList
            uv run pytest evals/regression/test_thresholds.py @ArgsList
        }
    }

    "eval-regression-suite" {
        uv run python -m evals.harness.keys
        if ($LASTEXITCODE -eq 0) {
            uv run python -m evals suite @ArgsList
        }
    }

    "kitaru-bootstrap" {
        if (-not $KitaruApiUrl) { $KitaruApiUrl = Get-EnvValue "KITARU_API_URL" }
        if (-not $KitaruApiUrl) { $KitaruApiUrl = $KitaruLocalUrl }

        uv run python scripts/bootstrap_kitaru.py --server $KitaruApiUrl @ArgsList
        
        Write-Host ""
        Write-Host "# Put these two in .env:" -ForegroundColor Yellow
        Write-Host "KITARU_API_URL=$KitaruApiUrl"
        
        $agentJson = uv run kitaru agent get decode --server $KitaruApiUrl --output json 2>$null
        $agentId = "<no decode agent there yet: check uv run kitaru status>"
        if ($agentJson) {
            $agentId = uv run python -c "import json,sys; d=sys.stdin.read().strip(); print(json.loads(d)['item']['id'] if d else '')" $agentJson
        }
        Write-Host "KITARU_AGENT_ID=$agentId"
    }

    "kitaru-local" {
        uv run kitaru login --local
        & $PSCommandPath kitaru-bootstrap -KitaruApiUrl $KitaruLocalUrl
    }

    "kitaru-seed" {
        uv run python scripts/seed_kitaru_sessions.py @ArgsList
    }

    "test" {
        uv run pytest @ArgsList
    }

    "unit-tests" {
        uv run pytest tests/unit @ArgsList
    }

    "integration-tests" {
        uv run pytest tests/integration @ArgsList
    }

    "lint-check" {
        uv run ruff check
    }

    "lint-fix" {
        uv run ruff check --fix
    }

    "format-check" {
        uv run ruff format --check
    }

    "format-fix" {
        uv run ruff format
    }

    "pre-commit" {
        & $PSCommandPath format-check
        & $PSCommandPath lint-check
        & $PSCommandPath unit-tests
    }

    "ci" {
        uv lock --check
        & $PSCommandPath format-check
        & $PSCommandPath lint-check
        & $PSCommandPath test
    }

    "help" {
        Write-Host "Available targets:" -ForegroundColor Cyan
        Write-Host "  install                  Install/refresh venv and git hooks"
        Write-Host "  build                    Build wheel + sdist into dist/"
        Write-Host "  install-cli              Install editable CLI executable"
        Write-Host "  uninstall-cli            Uninstall CLI executable"
        Write-Host "  test                     Run full pytest suite"
        Write-Host "  unit-tests               Run unit tests only"
        Write-Host "  integration-tests        Run integration tests only"
        Write-Host "  lint-check / lint-fix    Check or auto-fix ruff lint errors"
        Write-Host "  format-check / format-fix Check or format code with ruff"
        Write-Host "  pre-commit               Run format, lint, and unit tests"
        Write-Host "  ci                       Run lockfile check, format, lint, and tests"
        Write-Host "  kitaru-local             Start local Kitaru server and register agent"
        Write-Host "  kitaru-bootstrap         Register agent on KITARU_API_URL"
        Write-Host "  kitaru-seed              Record test sessions on server"
        Write-Host "  eval-benchmark           Run Langfuse benchmark experiment"
        Write-Host "  eval-regression-dataset  Sync cases & run threshold gate"
        Write-Host "  eval-regression-suite    Run Langfuse regression suite"
    }
}
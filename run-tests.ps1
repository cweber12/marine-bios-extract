<#
.SYNOPSIS
    Run the marine-bios-extract test suite.

.DESCRIPTION
    Tests that reach the network are marked with the "network" marker and are
    excluded by default, so a green run means nothing about upstream
    availability. Include them with -Network before trusting a release.

.EXAMPLE
    .\run-tests.ps1
    .\run-tests.ps1 -Network
#>

param(
    [switch]$Network
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $root ".venv\Scripts\python.exe"

if (-not (Test-Path $python)) {
    Write-Host "No virtual environment found at .venv\" -ForegroundColor Yellow
    Write-Host "    python -m venv .venv" -ForegroundColor Cyan
    Write-Host "    .venv\Scripts\python.exe -m pip install -e `".[test]`"" -ForegroundColor Cyan
    exit 1
}

# pytest is an optional extra, so a plain `pip install -e .` leaves it out.
& $python -c "import pytest" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "pytest is not installed in .venv" -ForegroundColor Yellow
    Write-Host "Install the test extra with:" -ForegroundColor Yellow
    Write-Host "    .venv\Scripts\python.exe -m pip install -e `".[test]`"" -ForegroundColor Cyan
    exit 1
}

$env:PYTHONPATH = Join-Path $root "src"

if ($Network) {
    & $python -m pytest tests -v
} else {
    & $python -m pytest tests -v -m "not network"
}
exit $LASTEXITCODE

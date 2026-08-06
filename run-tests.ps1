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
    exit 1
}

$env:PYTHONPATH = Join-Path $root "src"

if ($Network) {
    & $python -m pytest tests -v
} else {
    & $python -m pytest tests -v -m "not network"
}
exit $LASTEXITCODE

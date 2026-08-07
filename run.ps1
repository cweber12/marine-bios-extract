<#
.SYNOPSIS
    Launcher for marine-bios-extract. Finds the venv, sets PYTHONPATH, runs the CLI.

.DESCRIPTION
    You do not need to activate anything. This script points Python at src/
    directly, so the toolkit works from a clean clone as long as .venv exists
    with the dependencies listed in pyproject.toml.

    All arguments are passed straight through to the CLI.

.EXAMPLE
    .\run.ps1 --help
    .\run.ps1 list
    .\run.ps1 extract --bbox '-117.40,32.76,-117.15,32.95' --datasets mpa,shoreline
    .\run.ps1 study --study latest --pad-km 10 --datasets mpa,shoreline --yes

.NOTES
    Quote comma-separated values. PowerShell parses a bare
    -117.40,32.76,-117.15,32.95 as an array and forwards four separate
    arguments, which the CLI rejects as unrecognized. Applies to --bbox,
    --center and --datasets.
#>

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $root ".venv\Scripts\python.exe"

if (-not (Test-Path $python)) {
    Write-Host "No virtual environment found at .venv\" -ForegroundColor Yellow
    Write-Host "Create one with:" -ForegroundColor Yellow
    Write-Host "    python -m venv .venv" -ForegroundColor Cyan
    Write-Host "    .venv\Scripts\python.exe -m pip install -e ." -ForegroundColor Cyan
    exit 1
}

# The vector stack is not optional; fail here with a fixable message rather
# than an ImportError traceback three calls deep.
& $python -c "import pyogrio, shapely" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "The vector dependencies are missing from .venv" -ForegroundColor Yellow
    Write-Host "Install them with:" -ForegroundColor Yellow
    Write-Host "    .venv\Scripts\python.exe -m pip install -e ." -ForegroundColor Cyan
    exit 1
}

$env:PYTHONPATH = Join-Path $root "src"
& $python -m biosextract @args
exit $LASTEXITCODE

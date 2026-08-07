<#
.SYNOPSIS
    Create the per-slice commit history for this repo.

.DESCRIPTION
    The repo was built through the Cowork device bridge, which cannot unlink
    files on a mounted folder. Git needs unlink for every lock file, so the
    bridge left a stale .git\index.lock behind and only the first commit landed.

    This script clears those leftovers and replays the intended slice history.
    Every git call is checked: if something fails, it says so and stops, rather
    than reporting success it did not achieve.

    Safe to re-run. Slices already committed are skipped.

.EXAMPLE
    .\finish-commits.ps1
    .\finish-commits.ps1 -WhatIf    # show what would be committed, change nothing
#>

param(
    [switch]$WhatIf
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot
$root = $PSScriptRoot
$gitDir = Join-Path $root ".git"

if (-not (Test-Path $gitDir)) {
    Write-Host "No git repository here. Run: git init" -ForegroundColor Red
    exit 1
}

# --- git helper that actually checks the exit code ----------------------------

function Invoke-Git {
    param([Parameter(Mandatory)][string[]]$Arguments, [switch]$AllowFail)

    # Git writes advisories to stderr while succeeding - "LF will be replaced by
    # CRLF", detached-HEAD notices, hint blocks. With $ErrorActionPreference set
    # to 'Stop', PowerShell promotes ANY native stderr line to a terminating
    # error, so a harmless warning aborts the run even though git exited 0.
    # Lower the preference for the duration of the call and judge git the only
    # way that is actually meaningful: by its exit code.
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        # Casting each record to string flattens ErrorRecord objects that
        # 2>&1 would otherwise leave in the pipeline.
        $output = @(& git @Arguments 2>&1 | ForEach-Object { "$_" })
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previous
    }

    if ($code -ne 0 -and -not $AllowFail) {
        Write-Host ""
        Write-Host "git $($Arguments -join ' ')  ->  exit $code" -ForegroundColor Red
        $output | ForEach-Object { Write-Host "  $_" -ForegroundColor Red }
        throw "git failed; stopping so the history is not left half-built."
    }

    # Surface advisories rather than hiding them, but do not let them stop us.
    if ($code -eq 0) {
        $output | Where-Object { $_ -match '^(warning|hint):' } |
            ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
    }

    [pscustomobject]@{ Output = $output; ExitCode = $code }
}

# --- 1. clear what the bridge could not delete --------------------------------

Write-Host "Clearing bridge leftovers..." -ForegroundColor Cyan

$running = Get-Process -Name git -ErrorAction SilentlyContinue
if ($running) {
    Write-Host "  A git process is actually running (PID $($running.Id -join ', '))." -ForegroundColor Yellow
    Write-Host "  Close it before continuing - the locks below may be legitimate." -ForegroundColor Yellow
    exit 1
}

$stale = @()
foreach ($name in @("index.lock", "HEAD.lock", "config.lock", "ORIG_HEAD.lock")) {
    $p = Join-Path $gitDir $name
    if (Test-Path $p) { $stale += $p }
}
$stale += @(Get-ChildItem -Path $gitDir -Recurse -Filter "*.lock" -File -ErrorAction SilentlyContinue |
            Select-Object -ExpandProperty FullName)
# Half-written loose objects the bridge could not clean up.
$stale += @(Get-ChildItem -Path (Join-Path $gitDir "objects") -Recurse -Filter "tmp_obj_*" -File -ErrorAction SilentlyContinue |
            Select-Object -ExpandProperty FullName)
$stale = $stale | Sort-Object -Unique

foreach ($path in $stale) {
    try {
        Remove-Item -LiteralPath $path -Force -ErrorAction Stop
        Write-Host "  removed $($path.Substring($root.Length + 1))" -ForegroundColor DarkGray
    } catch {
        Write-Host "  COULD NOT REMOVE $path" -ForegroundColor Red
        Write-Host "  $($_.Exception.Message)" -ForegroundColor Red
        Write-Host "  Delete it in Explorer, then re-run this script." -ForegroundColor Yellow
        exit 1
    }
}
if (-not $stale) { Write-Host "  none found" -ForegroundColor DarkGray }

if (Test-Path (Join-Path $root "_to_delete")) {
    Remove-Item -Recurse -Force (Join-Path $root "_to_delete")
    Write-Host "  removed _to_delete\" -ForegroundColor DarkGray
}

# Prove git is usable before touching history.
Invoke-Git @("status", "--porcelain") | Out-Null
Write-Host "  git is responsive" -ForegroundColor Green

# --- 2. the slice history -----------------------------------------------------

$slices = @(
    @{ m = "feat: bounding box parsing, validation and CRS derivation"
       f = @("src/biosextract/bbox.py", "tests/__init__.py", "tests/conftest.py", "tests/test_bbox.py") },
    @{ m = "feat: dataset registry resolving BIOS URLs instead of constructing them"
       f = @("src/biosextract/catalog.py", "tests/test_catalog.py") },
    @{ m = "feat: validated caching download and in-place archive inspection"
       f = @("src/biosextract/fetch.py", "src/biosextract/archive.py", "tests/fixtures.py",
             "tests/test_fetch.py", "tests/test_archive.py") },
    @{ m = "feat: vector clipping with recomputed geometry attributes"
       f = @("src/biosextract/vector.py", "tests/test_vector.py") },
    @{ m = "feat: raster clipping on a bbox-anchored grid"
       f = @("src/biosextract/raster.py", "tests/test_raster.py") },
    @{ m = "feat: geojson, csv, gpkg, kmz and shapefile writers plus manifest"
       f = @("src/biosextract/outputs.py", "src/biosextract/manifest.py", "tests/test_outputs.py") },
    @{ m = "feat: cli with config file support and negative-longitude handling"
       f = @("src/biosextract/cli.py", "config.example.toml") },
    @{ m = "feat: citation extraction, licence and use-constraint reporting"
       f = @("src/biosextract/citation.py", "tests/test_citation.py") },
    @{ m = "feat: declare network behaviour and honour a BIOS_CONTACT user agent"
       f = @("pyproject.toml", "run-tests.ps1") },
    @{ m = "docs: readme, working agreement and commit helper"
       f = @("README.md", "CLAUDE.md", "finish-commits.ps1", ".gitattributes") }
)

if ($WhatIf) {
    Write-Host "`nWould commit, in order:`n" -ForegroundColor Cyan
    foreach ($s in $slices) {
        $have = @($s.f | Where-Object { Test-Path $_ })
        Write-Host "  $($s.m)"
        $have | ForEach-Object { Write-Host "      $_" -ForegroundColor DarkGray }
        $missing = @($s.f | Where-Object { -not (Test-Path $_) })
        $missing | ForEach-Object { Write-Host "      $_  (missing)" -ForegroundColor Yellow }
    }
    exit 0
}

# The bridge left everything staged; start clean so each slice commits only its
# own files rather than sweeping the whole tree into the first commit.
Write-Host "`nResetting the index..." -ForegroundColor Cyan
Invoke-Git @("reset", "-q") | Out-Null

Write-Host "Building history...`n" -ForegroundColor Cyan
$committed = 0
$skipped = 0

foreach ($slice in $slices) {
    $present = @($slice.f | Where-Object { Test-Path $_ })
    $missing = @($slice.f | Where-Object { -not (Test-Path $_) })
    foreach ($m in $missing) {
        Write-Host "  warning: $m does not exist, not committing it" -ForegroundColor Yellow
    }
    if (-not $present) {
        Write-Host "skip (no files): $($slice.m)" -ForegroundColor DarkGray
        $skipped++
        continue
    }

    Invoke-Git (@("add", "--") + $present) | Out-Null

    # Exit 1 from --quiet means "there are staged changes", which is what we want.
    $diff = Invoke-Git @("diff", "--cached", "--quiet") -AllowFail
    if ($diff.ExitCode -eq 0) {
        Write-Host "skip (already committed): $($slice.m)" -ForegroundColor DarkGray
        $skipped++
        continue
    }

    Invoke-Git @("commit", "-q", "-m", $slice.m) | Out-Null
    Write-Host "committed: $($slice.m)" -ForegroundColor Green
    $committed++
}

# --- 3. report what actually happened ----------------------------------------

Write-Host "`n$committed committed, $skipped skipped.`n" -ForegroundColor Cyan
Invoke-Git @("log", "--oneline") | Select-Object -ExpandProperty Output | ForEach-Object { Write-Host "  $_" }

$leftover = (Invoke-Git @("status", "--porcelain")).Output
Write-Host ""
if ($leftover) {
    Write-Host "Still uncommitted:" -ForegroundColor Yellow
    $leftover | ForEach-Object { Write-Host "  $_" -ForegroundColor Yellow }
    Write-Host "`nIf these are files you want tracked, add them by hand:" -ForegroundColor Yellow
    Write-Host "    git add -A; git commit -m 'chore: remaining files'" -ForegroundColor Cyan
} else {
    Write-Host "Working tree clean." -ForegroundColor Green
}

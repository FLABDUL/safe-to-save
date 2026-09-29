param([switch]$UnitOnly)
$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "Expected virtual environment interpreter at $python"
}

$envFile = Join-Path $projectRoot ".env.local"
if (Test-Path -LiteralPath $envFile) {
    Get-Content -LiteralPath $envFile | ForEach-Object {
        if ($_ -match '^\s*DATABASE_URL\s*=\s*(.*?)\s*$') {
            $env:DATABASE_URL = $Matches[1].Trim('"').Trim("'")
        }
    }
}

Push-Location $projectRoot
try {
    & $python -m ruff check src tests
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    & $python -m pytest -m "not integration" -v
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    if ($UnitOnly) {
        Write-Output 'Unit-only verification completed; integration was explicitly excluded.'
    } elseif ($env:DATABASE_URL) {
        & $python -c 'import os; from safe_to_save.db import isolated_dsn; isolated_dsn(os.environ["DATABASE_URL"])'
        if ($LASTEXITCODE -ne 0) { throw "Refusing integration tests outside isolated database." }
        & $python -m pytest -m integration -v
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    } else {
        throw 'Full verification requires the isolated database. Initialise it first, or request -UnitOnly explicitly.'
    }
} finally {
    Pop-Location
}

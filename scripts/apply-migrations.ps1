. (Join-Path $PSScriptRoot 'local-db-common.ps1')
Assert-RunningTaskCluster
$previous = $env:DATABASE_URL
try {
    $env:DATABASE_URL = Read-TaskDatabaseUrl
    $python = Join-Path $project '.venv\Scripts\python.exe'
    Push-Location $project
    try {
        & $python -c 'import os; from pathlib import Path; from safe_to_save.db import apply_migrations; print("Applied:", apply_migrations(os.environ["DATABASE_URL"], Path("sql")))'
        if ($LASTEXITCODE -ne 0) { throw 'Migration application failed.' }
    } finally { Pop-Location }
} finally { $env:DATABASE_URL = $previous }

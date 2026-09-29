. (Join-Path $PSScriptRoot 'local-db-common.ps1')
Assert-TaskCluster
if (-not (Test-Path -LiteralPath (Join-Path $data 'postmaster.pid'))) {
    Write-Output 'Task-local PostgreSQL is already stopped.'
    return
}
Assert-RunningTaskCluster
& (Join-Path $pgBin 'pg_ctl.exe') -D $data stop --wait -m fast
if ($LASTEXITCODE -ne 0) { throw 'Task-local PostgreSQL shutdown failed.' }

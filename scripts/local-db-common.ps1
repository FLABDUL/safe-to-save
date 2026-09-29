$ErrorActionPreference = 'Stop'
$project = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$pgBin = 'C:\Program Files\PostgreSQL\14\bin'
$data = [IO.Path]::GetFullPath((Join-Path $project '.postgres-data'))
$logDirectory = [IO.Path]::GetFullPath((Join-Path $project '.postgres-log'))
$database = 'safe_to_save'
$admin = 'sts_admin'

function Assert-TaskPath([string]$Path) {
    $resolved = [IO.Path]::GetFullPath($Path)
    if (-not $resolved.StartsWith($project + [IO.Path]::DirectorySeparatorChar,
        [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Database path must remain inside the Safe to Save project.'
    }
    $candidate = $resolved
    while ($candidate.Length -gt $project.Length) {
        if (Test-Path -LiteralPath $candidate) {
            $item = Get-Item -Force -LiteralPath $candidate
            # OneDrive marks hydrated directories as reparse points without making
            # them links. Reject only entries that actually resolve through a link
            # or junction; the project boundary and cluster marker are checked below.
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -and
                ($item.LinkType -or $item.Target)) {
                throw 'Database paths must not traverse links or junctions.'
            }
        }
        $candidate = Split-Path -Parent $candidate
    }
}

function Assert-TaskCluster {
    Assert-TaskPath $data
    $marker = Join-Path $data 'safe-to-save-cluster.txt'
    if (-not (Test-Path -LiteralPath (Join-Path $data 'PG_VERSION')) -or
        -not (Test-Path -LiteralPath $marker) -or
        (Get-Content -Raw -LiteralPath $marker).Trim() -ne $data) {
        throw 'Expected the explicitly marked task-local PostgreSQL cluster.'
    }
    if ((Get-Content -Raw -LiteralPath (Join-Path $data 'PG_VERSION')).Trim() -ne '14') {
        throw 'Expected PostgreSQL 14 task-local state.'
    }
}

function Assert-RunningTaskCluster {
    Assert-TaskCluster
    $pidPath = Join-Path $data 'postmaster.pid'
    if (-not (Test-Path -LiteralPath $pidPath)) { throw 'Task-local cluster is not running.' }
    $state = @(Get-Content -LiteralPath $pidPath)
    if ($state.Count -lt 6 -or [IO.Path]::GetFullPath($state[1]) -ne $data -or
        $state[3] -ne '55433' -or $state[5] -ne '127.0.0.1') {
        throw 'Running cluster does not match the task-local path and endpoint.'
    }
}

function Read-TaskDatabaseUrl {
    $envPath = Join-Path $project '.env.local'
    Assert-TaskPath $envPath
    if (-not (Test-Path -LiteralPath $envPath)) { throw 'Run init-local-db.ps1 first.' }
    $lines = @(Get-Content -LiteralPath $envPath | Where-Object { $_ -match '^DATABASE_URL=' })
    if ($lines.Count -ne 1) { throw 'Expected one DATABASE_URL in .env.local.' }
    $value = $lines[0].Substring('DATABASE_URL='.Length)
    # The generated URL has a hexadecimal password; never print it or errors containing it.
    if ($value -notmatch '^postgresql://sts_admin:([a-f0-9]{64})@127\.0\.0\.1:55433/safe_to_save$') {
        throw 'Unexpected task-local database configuration.'
    }
    return $value
}

Assert-TaskPath $data
Assert-TaskPath $logDirectory

param([int]$Port = 55433)
. (Join-Path $PSScriptRoot 'local-db-common.ps1')
if ($Port -ne 55433) { throw 'Only isolated port 55433 is permitted.' }
foreach ($name in @('initdb.exe', 'pg_ctl.exe', 'psql.exe', 'createdb.exe')) {
    if (-not (Test-Path -LiteralPath (Join-Path $pgBin $name))) {
        throw 'PostgreSQL 14 executables are required.'
    }
}

$envPath = Join-Path $project '.env.local'
Assert-TaskPath $envPath
$newCluster = -not (Test-Path -LiteralPath $data)
if ($newCluster) {
    if (Test-Path -LiteralPath $envPath) {
        throw 'Existing environment without task-local cluster; refusing to overwrite it.'
    }
    if (Get-NetTCPConnection -LocalPort 55433 -State Listen -ErrorAction SilentlyContinue) {
        throw 'Port 55433 is already occupied; no existing cluster will be reused.'
    }
    New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
    $bytes = New-Object byte[] 32
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    $rng.GetBytes($bytes)
    $rng.Dispose()
    $password = ([BitConverter]::ToString($bytes)).Replace('-', '').ToLowerInvariant()
    $passwordFile = Join-Path $logDirectory 'init-password.tmp'
    Assert-TaskPath $passwordFile
    try {
        [IO.File]::WriteAllText($passwordFile, $password + "`n")
        & (Join-Path $pgBin 'initdb.exe') -D $data -U $admin --encoding=UTF8 --locale=C `
            --auth-host=scram-sha-256 --auth-local=scram-sha-256 --pwfile=$passwordFile
        if ($LASTEXITCODE -ne 0) { throw 'Task-local initdb failed.' }
        [IO.File]::WriteAllText((Join-Path $data 'safe-to-save-cluster.txt'), $data)
        [IO.File]::AppendAllText((Join-Path $data 'postgresql.conf'),
            "`nlisten_addresses = '127.0.0.1'`nport = 55433`npassword_encryption = 'scram-sha-256'`n")
        [IO.File]::WriteAllText($envPath,
            "DATABASE_URL=postgresql://${admin}:${password}@127.0.0.1:55433/${database}`n")
    } finally {
        if (Test-Path -LiteralPath $passwordFile) { Remove-Item -LiteralPath $passwordFile }
    }
}

Assert-TaskCluster
$databaseUrl = Read-TaskDatabaseUrl
if (Test-Path -LiteralPath (Join-Path $data 'postmaster.pid')) {
    Assert-RunningTaskCluster
    & (Join-Path $pgBin 'pg_ctl.exe') -D $data status
    if ($LASTEXITCODE -ne 0) { throw 'Task-local cluster PID exists but server is not running.' }
} else {
    if (Get-NetTCPConnection -LocalPort 55433 -State Listen -ErrorAction SilentlyContinue) {
        throw 'Port 55433 is already occupied; refusing to reuse another process.'
    }
    New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
    $log = Join-Path $logDirectory 'server.log'
    Assert-TaskPath $log
    & (Join-Path $pgBin 'pg_ctl.exe') -D $data -l $log -o '-h 127.0.0.1 -p 55433' start --wait
    if ($LASTEXITCODE -ne 0) { throw 'Task-local PostgreSQL startup failed.' }
    Assert-RunningTaskCluster
}

# Creation needs the new cluster's default administrative database. All application
# connections remain pinned to safe_to_save. Ignore inherited libpq routing overrides.
$savedPg = @{}
foreach ($name in @('PGPASSWORD', 'PGHOSTADDR', 'PGSERVICE', 'PGSERVICEFILE', 'PGOPTIONS')) {
    $savedPg[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
    Remove-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue
}
try {
    $env:PGPASSWORD = ([Uri]$databaseUrl).UserInfo.Split(':')[1]
    $env:PGHOSTADDR = '127.0.0.1'
    Assert-RunningTaskCluster
    $exists = & (Join-Path $pgBin 'psql.exe') -X -h 127.0.0.1 -p 55433 -U $admin -d postgres `
        -w -tAc "SELECT 1 FROM pg_database WHERE datname = 'safe_to_save'"
    if ($LASTEXITCODE -ne 0) { throw 'Task-local administrative connection failed.' }
    if ($exists -ne '1') {
        & (Join-Path $pgBin 'createdb.exe') -h 127.0.0.1 -p 55433 -U $admin -w $database
        if ($LASTEXITCODE -ne 0) { throw 'Task-local database creation failed.' }
    }
} finally {
    foreach ($name in $savedPg.Keys) {
        if ($null -eq $savedPg[$name]) {
            Remove-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue
        } else {
            [Environment]::SetEnvironmentVariable($name, $savedPg[$name], 'Process')
        }
    }
}
Write-Output 'Safe to Save PostgreSQL ready at 127.0.0.1:55433/safe_to_save.'

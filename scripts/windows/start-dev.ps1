#Requires -Version 5.1
<#
.SYNOPSIS
    FormuMind dev runner for Windows: start / stop / status of the host-side stack.

.DESCRIPTION
    Runs backend (uvicorn), Celery worker and the Vite dev server as host
    processes, with the infrastructure (Redis, Neo4j, optional Datalab ELN and
    the MolScribe worker) left to Docker. Mirrors scripts/dev/start-dev.sh.

    Windows-specific handling that this script exists for:

      * Celery must use --pool=solo. The default prefork pool cannot run on
        Windows (daemonic processes cannot have children).
      * uvicorn uses --reload-dir app instead of --reload-exclude, because
        PowerShell expands a bare `.venv\*` into a real file list before uvicorn
        ever sees it ("Got unexpected extra arguments (.venv\Lib ... )").
      * Env vars are set with `$env:` in the launcher session; Start-Process
        children inherit them, so no bash-style `VAR=value cmd` prefix is needed.
      * The DB URL is made absolute. A relative sqlite URL resolves against each
        process's CWD, so uvicorn and Celery would otherwise open different files.

.PARAMETER Action
    start (default) | stop | status | restart

.PARAMETER NoFrontend
    Do not start the Vite dev server.

.PARAMETER NoInfra
    Do not touch Docker infra (Redis/Neo4j). Use when the containers are managed
    elsewhere.

.PARAMETER RequireEln
    Treat an unreachable Datalab ELN as fatal instead of degrading to the local
    sqlite ledger.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\windows\start-dev.ps1 start

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\windows\start-dev.ps1 status
#>
[CmdletBinding()]
param(
    [ValidateSet('start', 'stop', 'status', 'restart')]
    [string]$Action = 'start',
    [switch]$NoFrontend,
    [switch]$NoInfra,
    [switch]$RequireEln
)

$ErrorActionPreference = 'Stop'

# ---------------------------------------------------------------- helpers ----

function Write-Step  { param([string]$Message) Write-Host "==> $Message" -ForegroundColor Cyan }
function Write-Ok    { param([string]$Message) Write-Host "    $Message" -ForegroundColor Green }
function Write-Note  { param([string]$Message) Write-Host "    $Message" -ForegroundColor Gray }
function Write-Warn2 { param([string]$Message) Write-Host "WARN $Message" -ForegroundColor Yellow }
function Write-Err   { param([string]$Message) Write-Host "ERR  $Message" -ForegroundColor Red }

function Invoke-Streamed {
    <#
        Run a native command whose stderr carries ordinary progress output (docker
        compose, alembic) and echo every line. Merging stderr with `2>&1` turns those
        lines into ErrorRecords, which `$ErrorActionPreference = 'Stop'` would promote
        to a terminating error — aborting a perfectly healthy start-up. The preference
        is therefore relaxed for the duration of the call only.
    #>
    param([Parameter(Mandatory = $true)][scriptblock]$Body)
    $saved = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & $Body } finally { $ErrorActionPreference = $saved }
}

function Test-PortOpen {
    param([string]$ComputerName = '127.0.0.1', [int]$Port, [int]$TimeoutMs = 1200)
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $iar = $client.BeginConnect($ComputerName, $Port, $null, $null)
        if (-not $iar.AsyncWaitHandle.WaitOne($TimeoutMs)) { return $false }
        $client.EndConnect($iar)
        return $true
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

function Test-HttpOk {
    param([string]$Url, [int]$TimeoutSec = 3)
    try {
        $resp = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec $TimeoutSec
        return ($resp.StatusCode -ge 200 -and $resp.StatusCode -lt 400)
    } catch {
        return $false
    }
}

function Wait-For {
    param([scriptblock]$Condition, [int]$TimeoutSec = 60, [string]$Label = 'service')
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        if (& $Condition) { return $true }
        Start-Sleep -Seconds 1
    }
    return $false
}

# ------------------------------------------------------------------- setup ---

$Root     = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$ScriptName = Split-Path -Leaf $PSCommandPath
$Backend  = Join-Path $Root 'backend'
$Frontend = Join-Path $Root 'frontend'
$Logs     = Join-Path $Root 'logs'
$Pids     = Join-Path $Logs 'pids'
$VenvPy   = Join-Path $Backend '.venv\Scripts\python.exe'

New-Item -ItemType Directory -Force -Path $Logs | Out-Null
New-Item -ItemType Directory -Force -Path $Pids | Out-Null

$ApiPort  = 8000
$VitePort = 5173
$PidFiles = @{
    api    = Join-Path $Pids 'api.pid'
    worker = Join-Path $Pids 'worker.pid'
    vite   = Join-Path $Pids 'vite.pid'
}

function Get-RecordedProcess {
    param([string]$Kind)
    $file = $PidFiles[$Kind]
    if (-not (Test-Path $file)) { return $null }
    $raw = (Get-Content $file -ErrorAction SilentlyContinue | Select-Object -First 1)
    if (-not $raw) { return $null }
    $procId = 0
    if (-not [int]::TryParse($raw.Trim(), [ref]$procId)) { return $null }
    $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
    if ($proc) { return $proc }
    Remove-Item $file -ErrorAction SilentlyContinue
    return $null
}

function Stop-Tree {
    param([System.Diagnostics.Process]$Process)
    if (-not $Process) { return $false }
    # Kill the whole tree: npm.cmd spawns node/vite, python -m celery spawns children.
    # Invoke-Streamed because taskkill reports ordinary failures (already-exited pid) on
    # stderr, which a Stop preference would turn into a terminating error.
    Invoke-Streamed { & taskkill.exe /PID $Process.Id /T /F 2>&1 | Out-Null }
    return ($LASTEXITCODE -eq 0)
}

function Stop-ByPort {
    param([int]$Port)
    $conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if (-not $conns) { return $false }
    $stopped = $false
    foreach ($c in ($conns | Select-Object -ExpandProperty OwningProcess -Unique)) {
        $p = Get-Process -Id $c -ErrorAction SilentlyContinue
        if ($p) { $stopped = (Stop-Tree -Process $p) -or $stopped }
    }
    return $stopped
}

# ------------------------------------------------------------- environment ---

function Test-EnvFileSets {
    # Whether the .env file the settings read assigns $Key (a commented-out line does not count).
    param([string]$Key)
    $file = $env:FORMUMIND_ENV_FILE
    if (-not $file -or -not (Test-Path $file)) { return $false }
    return [bool](Select-String -Path $file -Pattern "^\s*$Key\s*=" -Quiet)
}

function Set-FormuMindEnv {
    $rootSlash = $Root -replace '\\', '/'

    # Canonical env file: root .env wins, then data\.env (matches config.resolve_env_path).
    foreach ($candidate in @((Join-Path $Root '.env'), (Join-Path $Root 'data\.env'))) {
        if (Test-Path $candidate) { $env:FORMUMIND_ENV_FILE = $candidate; break }
    }

    # Absolute DB path: a relative sqlite URL resolves against each process CWD,
    # so uvicorn and Celery would silently use different files.
    $dataDir = Join-Path $Root 'data'
    New-Item -ItemType Directory -Force -Path $dataDir | Out-Null
    $env:FORMUMIND_DB_URL = "sqlite:///$rootSlash/data/formumind.db"
    $env:FORMUMIND_COLBERT_INDEX_DIR = Join-Path $dataDir 'colbert_index'

    $env:FORMUMIND_CELERY_EAGER = 'false'

    # `localhost` resolves to ::1 first on Windows, and a Redis that listens on 127.0.0.1 only refuses that - slowly: a refused
    # connection takes ~0.5 s there. The API paid it on every /health call (513 ms each, measured by smoke-stack.ps1) and on every
    # dispatch, and the worker on every Redis connection. Name the IPv4 loopback - unless the user chose a Redis themselves,
    # in the environment or in the .env file the settings read.
    if (-not $env:FORMUMIND_REDIS_URL -and -not (Test-EnvFileSets -Key 'FORMUMIND_REDIS_URL')) {
        $env:FORMUMIND_REDIS_URL = 'redis://127.0.0.1:6379/0'
    }

    $env:FORMUMIND_DATALAB_API_URL = if ($env:FORMUMIND_DATALAB_API_URL) { $env:FORMUMIND_DATALAB_API_URL } else { 'http://127.0.0.1:5001' }
}

function Resolve-ElnMode {
    <#
        Datalab is a core dependency for lab/recommend/workbench/optimize, but a
        Windows dev box frequently has no ELN running. Probe it: when reachable,
        run in ELN mode; otherwise degrade to the local sqlite ledger instead of
        failing to start (the product default is auto + REQUIRED=false).
    #>
    $url = $env:FORMUMIND_DATALAB_API_URL
    $reachable = Test-HttpOk -Url "$($url.TrimEnd('/'))/" -TimeoutSec 3
    if ($reachable) {
        $env:FORMUMIND_CAMPAIGN_BACKEND = 'datalab'
        $env:FORMUMIND_EXPERIMENT_BACKEND = 'datalab'
        $env:FORMUMIND_DATALAB_REQUIRED = 'true'
        Write-Ok "Datalab ELN reachable at $url - ELN mode"
        return
    }
    if ($RequireEln) {
        Write-Err "Datalab ELN unreachable at $url and -RequireEln was given."
        Write-Note 'Start it: docker compose -f docker-compose.yml -f docker-compose.eln.yml up -d'
        Write-Note 'See deploy/eln/README.md.'
        exit 1
    }
    $env:FORMUMIND_CAMPAIGN_BACKEND = 'auto'
    $env:FORMUMIND_EXPERIMENT_BACKEND = 'auto'
    $env:FORMUMIND_DATALAB_REQUIRED = 'false'
    Write-Warn2 "Datalab ELN unreachable at $url - starting in soft-degrade mode (local sqlite ledger)."
    Write-Note 'DOE/workbench stay usable; ELN-backed campaigns need: docker compose -f docker-compose.yml -f docker-compose.eln.yml up -d'
}

# ------------------------------------------------------------------- infra ---

function Start-Infra {
    if ($NoInfra) { Write-Note 'infra untouched (-NoInfra)'; return }
    Write-Step 'Infrastructure (Docker)'
    $docker = Get-Command docker -ErrorAction SilentlyContinue
    if (-not $docker) {
        Write-Warn2 'docker not found - Redis/Neo4j must already be running as local services.'
        Write-Note 'Redis for Windows: Memurai, or WSL2 `sudo apt install redis-server`.'
        Write-Note 'Neo4j: Neo4j Desktop/Service, or Docker Desktop.'
        return
    }
    & $docker.Source compose version *> $null
    if ($LASTEXITCODE -ne 0) { Write-Warn2 'docker compose unavailable - skipping infra'; return }

    Push-Location $Root
    try {
        # redis is mandatory (Celery broker + SSE pub/sub); kg (Neo4j) is the
        # graph store. MolScribe/ELN are started separately (heavy: ~1.9 GB / stack).
        Invoke-Streamed { & $docker.Source compose up -d redis kg 2>&1 | ForEach-Object { Write-Note $_ } }
    } finally {
        Pop-Location
    }

    if (Wait-For -Condition { Test-PortOpen -Port 6379 } -TimeoutSec 60 -Label 'redis') {
        Write-Ok 'Redis :6379 up'
    } else {
        Write-Err 'Redis :6379 not reachable - Celery and SSE progress will not work.'
        Write-Note 'docker compose up -d redis    |    or a local Redis/Memurai service'
        exit 1
    }
    if (Test-PortOpen -Port 7687) { Write-Ok 'Neo4j :7687 up' } else { Write-Warn2 'Neo4j :7687 not reachable (KG store will be unavailable)' }
}

# --------------------------------------------------------------- processes ---

function Start-Api {
    Write-Step 'Backend API (uvicorn)'
    $out = Join-Path $Logs 'uvicorn.log'
    $err = Join-Path $Logs 'uvicorn.err.log'
    $proc = Start-Process -FilePath $VenvPy -ArgumentList @(
        '-m', 'uvicorn', 'app.main:app',
        '--host', '127.0.0.1', '--port', "$ApiPort",
        '--reload', '--reload-dir', 'app'
    ) -WorkingDirectory $Backend -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $out -RedirectStandardError $err
    $proc.Id | Out-File -FilePath $PidFiles.api -Encoding ascii

    if (Wait-For -Condition { Test-HttpOk -Url "http://127.0.0.1:$ApiPort/health" -TimeoutSec 2 } -TimeoutSec 90 -Label 'api') {
        Write-Ok "API ready on http://127.0.0.1:$ApiPort  (log: $out)"
        try {
            $health = Invoke-RestMethod -Uri "http://127.0.0.1:$ApiPort/health" -TimeoutSec 5
            Write-Note "status=$($health.status) database=$($health.database.scheme) broker=$($health.task_broker.reachable) datalab=$($health.datalab.reachable)"
        } catch { }
    } else {
        Write-Warn2 "API did not answer /health within 90s - inspect $out and $err"
    }
}

function Start-Worker {
    Write-Step 'Celery worker (--pool=solo, required on Windows)'
    $out = Join-Path $Logs 'celery.log'
    $err = Join-Path $Logs 'celery.err.log'
    $proc = Start-Process -FilePath $VenvPy -ArgumentList @(
        '-m', 'celery', '-A', 'app.worker.celery_app.celery_app',
        'worker', '--loglevel=info', '--pool=solo'
    ) -WorkingDirectory $Backend -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $out -RedirectStandardError $err
    $proc.Id | Out-File -FilePath $PidFiles.worker -Encoding ascii

    # Celery logs through the logging module, i.e. to stderr: "celery@HOST ready." lands in the .err log. Looking only at
    # the stdout log made every start wait the full 60 s and then warn about a worker that was fine.
    $ready = Wait-For -Condition {
        foreach ($log in @($out, $err)) {
            if ((Test-Path $log) -and (Select-String -Path $log -Pattern 'ready\.' -Quiet -ErrorAction SilentlyContinue)) { return $true }
        }
        return $false
    } -TimeoutSec 60 -Label 'worker'
    if ($ready) { Write-Ok "worker ready (log: $out)" }
    else { Write-Warn2 "worker not confirmed ready within 60s - inspect $out and $err" }
}

function Start-Frontend {
    Write-Step 'Frontend (vite)'
    $npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if (-not $npm) { $npm = Get-Command npm -ErrorAction SilentlyContinue }
    if (-not $npm) {
        Write-Warn2 'npm not found - install Node.js 20+ (https://nodejs.org) then re-run.'
        return
    }
    $out = Join-Path $Logs 'vite.log'
    $err = Join-Path $Logs 'vite.err.log'
    $proc = Start-Process -FilePath $npm.Source -ArgumentList @(
        'run', 'dev', '--', '--host', '127.0.0.1', '--port', "$VitePort"
    ) -WorkingDirectory $Frontend -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $out -RedirectStandardError $err
    $proc.Id | Out-File -FilePath $PidFiles.vite -Encoding ascii

    if (Wait-For -Condition { Test-HttpOk -Url "http://127.0.0.1:$VitePort/" -TimeoutSec 2 } -TimeoutSec 90 -Label 'vite') {
        Write-Ok "frontend ready on http://127.0.0.1:$VitePort  (log: $out)"
    } else {
        Write-Warn2 "frontend did not answer within 90s - inspect $out and $err"
    }
}

# ------------------------------------------------------------------ actions ---

function Invoke-Start {
    Write-Host ''
    Write-Host 'FormuMind - dev start (Windows)' -ForegroundColor White
    Write-Note "repo root : $Root"
    Write-Host ''

    if (-not (Test-Path $VenvPy)) {
        Write-Err 'backend\.venv\Scripts\python.exe not found.'
        Write-Note 'Run the installer first (repo root):'
        Write-Note '  .\install.bat          (or: powershell -ExecutionPolicy Bypass -File install.ps1)'
        exit 1
    }

    Set-FormuMindEnv
    Start-Infra
    Resolve-ElnMode

    $apiRunning = Test-PortOpen -Port $ApiPort
    $viteRunning = Test-PortOpen -Port $VitePort

    if ($apiRunning) {
        Write-Warn2 "port $ApiPort already in use - skipping API start (run `"$ScriptName stop`" first)"
    } else {
        # Migrations before boot: a stale schema shows up as
        # "schema drift: 缺失列 document_chunks.bbox … 请先运行 alembic upgrade head".
        Write-Step 'Database migrations (alembic upgrade head)'
        Push-Location $Backend
        try {
            Invoke-Streamed { & $VenvPy -m alembic upgrade head 2>&1 | ForEach-Object { Write-Note $_ } }
            if ($LASTEXITCODE -ne 0) {
                Write-Warn2 'alembic failed - the API may still boot, but the KB schema could be stale.'
            } else {
                Write-Ok 'schema up to date'
            }
        } finally { Pop-Location }

        Start-Api
        Start-Worker
    }

    if (-not $NoFrontend) {
        if ($viteRunning) { Write-Warn2 "port $VitePort already in use - skipping frontend start" }
        else { Start-Frontend }
    }

    Write-Host ''
    Write-Host 'Stack up.' -ForegroundColor Green
    Write-Note "API       : http://127.0.0.1:$ApiPort        (docs: /docs, health: /health)"
    Write-Note "Frontend  : http://127.0.0.1:$VitePort"
    Write-Note "Logs      : $Logs"
    Write-Note 'Stop with : powershell -ExecutionPolicy Bypass -File scripts\windows\start-dev.ps1 stop'
    Write-Host ''
}

function Invoke-Stop {
    Write-Host ''
    Write-Step 'Stopping FormuMind host processes'
    $any = $false
    foreach ($kind in @('worker', 'api', 'vite')) {
        $proc = Get-RecordedProcess -Kind $kind
        if (-not $proc) { continue }
        if (Stop-Tree -Process $proc) { Write-Ok "stopped $kind (pid $($proc.Id))" } else { Write-Warn2 "could not stop $kind (pid $($proc.Id))" }
        Remove-Item $PidFiles[$kind] -ErrorAction SilentlyContinue
        $any = $true
    }
    # Fallback: anything still holding the dev ports (started outside this script).
    foreach ($port in @($ApiPort, $VitePort)) {
        if (Stop-ByPort -Port $port) { Write-Ok "freed port $port"; $any = $true }
    }
    if (-not $any) { Write-Note 'nothing to stop' }
    Write-Note 'Docker infra (redis/kg) left running; stop it with: docker compose stop redis kg'
    Write-Host ''
}

function Invoke-Status {
    Write-Host ''
    Write-Host 'FormuMind - status' -ForegroundColor White
    Write-Host ''

    $rows = @()
    $rows += [pscustomobject]@{ Component = 'API';      Check = ":8000"; State = if (Test-HttpOk -Url "http://127.0.0.1:$ApiPort/health" -TimeoutSec 2) { 'up' } else { 'down' } }
    $rows += [pscustomobject]@{ Component = 'Frontend'; Check = ":5173"; State = if (Test-HttpOk -Url "http://127.0.0.1:$VitePort/" -TimeoutSec 2) { 'up' } else { 'down' } }
    $rows += [pscustomobject]@{ Component = 'Redis';    Check = ':6379'; State = if (Test-PortOpen -Port 6379) { 'up' } else { 'down' } }
    $rows += [pscustomobject]@{ Component = 'Neo4j';    Check = ':7687'; State = if (Test-PortOpen -Port 7687) { 'up' } else { 'down' } }
    $rows += [pscustomobject]@{ Component = 'Datalab';  Check = ':5001'; State = if (Test-HttpOk -Url 'http://127.0.0.1:5001/' -TimeoutSec 2) { 'up' } else { 'down' } }

    foreach ($r in $rows) {
        $colour = if ($r.State -eq 'up') { 'Green' } else { 'Red' }
        Write-Host ("  {0,-10} {1,-8} {2}" -f $r.Component, $r.Check, $r.State) -ForegroundColor $colour
    }

    foreach ($kind in @('api', 'worker', 'vite')) {
        $proc = Get-RecordedProcess -Kind $kind
        if ($proc) { Write-Note "$kind pid $($proc.Id)" } else { Write-Note "$kind not started by this script" }
    }

    if (Test-HttpOk -Url "http://127.0.0.1:$ApiPort/health" -TimeoutSec 3) {
        try {
            $h = Invoke-RestMethod -Uri "http://127.0.0.1:$ApiPort/health" -TimeoutSec 5
            Write-Host ''
            Write-Note "health: status=$($h.status) db=$($h.database.scheme) broker=$($h.task_broker.reachable) datalab=$($h.datalab.reachable) ledger=$($h.datalab.ledger_mode)"
        } catch { }
    }

    $docker = Get-Command docker -ErrorAction SilentlyContinue
    if ($docker) {
        Write-Host ''
        Write-Note 'docker containers:'
        Invoke-Streamed { & $docker.Source ps --filter 'name=formumind' --format '    {{.Names}}  {{.Status}}' 2>&1 | ForEach-Object { Write-Host $_ -ForegroundColor Gray } }
    }
    Write-Host ''
}

switch ($Action) {
    'start'   { Invoke-Start }
    'stop'    { Invoke-Stop }
    'status'  { Invoke-Status }
    'restart' { Invoke-Stop; Start-Sleep -Seconds 2; Invoke-Start }
}

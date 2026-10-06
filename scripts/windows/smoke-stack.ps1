#Requires -Version 5.1
<#
.SYNOPSIS
    End-to-end check of the Windows host stack: start -> health -> a real background task through the Celery solo worker
    -> status -> stop -> nothing left running.

.DESCRIPTION
    Drives start-dev.ps1 exactly as a user does, then asks the running API for something only a working worker can answer:
    an uploaded text file is queued (HTTP 202), picked up by `celery --pool=solo` through Redis, parsed, and the task must
    reach `completed` with evidence in its result. Afterwards `stop` must leave no FormuMind process behind - the worker
    included (its PID used to go unrecorded, so every `start` left an orphan behind).

    Needs a Redis on :6379 (Memurai, Redis for Windows, or the one Docker Desktop runs) and the venv install.bat builds.
    Datalab, Neo4j and the frontend are not part of it: Datalab is a Linux-container product (see README.md here), and a
    missing one must only degrade the ledger, which the first check pins.

    Prints one PASS / FAIL line per check and exits 0 only when every check passed.

.PARAMETER TimeoutSec
    How long to wait for the uploaded file's task to finish.

.PARAMETER KeepRunning
    Leave the stack up afterwards (the stop checks are skipped).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\windows\smoke-stack.ps1
#>
[CmdletBinding()]
param(
    [int]$TimeoutSec = 180,
    [switch]$KeepRunning
)

$ErrorActionPreference = 'Stop'

$Root     = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$Runner   = Join-Path $PSScriptRoot 'start-dev.ps1'
$Logs     = Join-Path $Root 'logs'
$PidDir   = Join-Path $Logs 'pids'
$VenvDir  = Join-Path $Root 'backend\.venv'
$Api      = 'http://127.0.0.1:8000'
$script:Failed = New-Object System.Collections.Generic.List[string]

function Check {
    param([string]$Name, [bool]$Ok, [string]$Detail = '')
    if ($Ok) {
        Write-Host "PASS  $Name" -ForegroundColor Green
    } else {
        Write-Host "FAIL  $Name  $Detail" -ForegroundColor Red
        $script:Failed.Add($Name)
    }
}

function Invoke-Runner {
    # The runner as a user runs it: its own powershell.exe, execution policy bypassed. Stderr is merged, so the preference is
    # relaxed for the call (a native command's stderr is an ErrorRecord, which Stop turns into a terminating error).
    param([string]$Action, [string[]]$Extra = @())
    $argv = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $Runner, $Action) + $Extra
    $saved = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $text = (& powershell.exe @argv 2>&1 | Out-String)
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $saved
    }
    Write-Host $text
    return @{ Code = $code; Text = $text }
}

function Get-PidFromFile {
    param([string]$Kind)
    $file = Join-Path $PidDir "$Kind.pid"
    if (-not (Test-Path $file)) { return 0 }
    $raw = (Get-Content $file -TotalCount 1)
    $value = 0
    if ($raw -and [int]::TryParse($raw.Trim(), [ref]$value)) { return $value }
    return 0
}

function Test-PortOpen {
    param([int]$Port)
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $iar = $client.BeginConnect('127.0.0.1', $Port, $null, $null)
        if (-not $iar.AsyncWaitHandle.WaitOne(1500)) { return $false }
        $client.EndConnect($iar)
        return $true
    } catch { return $false } finally { $client.Close() }
}

function Get-VenvProcesses {
    # Anything running out of this checkout's venv: uvicorn, its reload child, the Celery worker.
    $prefix = (Join-Path $VenvDir 'Scripts') + '\'
    return @(Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase) })
}

# ---------------------------------------------------------------- preconditions

Write-Host "FormuMind - Windows stack smoke test" -ForegroundColor White
Write-Host "repo root: $Root"
Check 'the installer built the venv' (Test-Path (Join-Path $VenvDir 'Scripts\python.exe')) 'run install.bat first'
Check 'a Redis answers on :6379' (Test-PortOpen -Port 6379) 'install Memurai / Redis for Windows, or start Docker Desktop and `docker compose up -d redis`'
if ($script:Failed.Count -gt 0) {
    Write-Host "`nCannot go on without those." -ForegroundColor Red
    exit 1
}
if (-not $env:FORMUMIND_API_AUTH_ENABLED) { $env:FORMUMIND_API_AUTH_ENABLED = 'false' }  # the checks below send no token

# ---------------------------------------------------------------- start

$start = Invoke-Runner -Action 'start' -Extra @('-NoInfra', '-NoFrontend')
Check 'start exits 0 and reaches "Stack up."' (($start.Code -eq 0) -and ($start.Text -match 'Stack up\.')) "exit code $($start.Code)"
Check 'the missing Datalab only degrades the ledger (no ELN here)' ($start.Text -match 'soft-degrade|ELN mode') 'the runner printed neither mode'
Check 'the worker was confirmed ready (no 60 s timeout warning)' ($start.Text -notmatch 'worker not confirmed ready') 'celery never logged "ready."'

$apiPid = Get-PidFromFile -Kind 'api'
$workerPid = Get-PidFromFile -Kind 'worker'
Check 'the API pid was recorded and the process is alive' (($apiPid -gt 0) -and [bool](Get-Process -Id $apiPid -ErrorAction SilentlyContinue)) "pid file says $apiPid"
Check 'the worker pid was recorded and the process is alive' (($workerPid -gt 0) -and [bool](Get-Process -Id $workerPid -ErrorAction SilentlyContinue)) "pid file says $workerPid"

# ---------------------------------------------------------------- health

$health = $null
try { $health = Invoke-RestMethod -Uri "$Api/health" -TimeoutSec 10 } catch { Write-Host "health: $($_.Exception.Message)" }
Check '/health answers' ($null -ne $health)
if ($health) {
    Check 'the task broker is required (not eager) and reachable' (($health.task_broker.required -eq $true) -and ($health.task_broker.reachable -eq $true)) ($health.task_broker | ConvertTo-Json -Compress)
    Check 'the database is ok' ($health.database.ok -eq $true)
    $timer = [System.Diagnostics.Stopwatch]::StartNew()
    1..3 | ForEach-Object { Invoke-RestMethod -Uri "$Api/health" -TimeoutSec 10 | Out-Null }
    Write-Host ("info  /health takes {0:N0} ms per call (the broker probe is a socket connect each time)" -f ($timer.ElapsedMilliseconds / 3))
}

# ---------------------------------------------------------------- a real task through the solo worker

$sample = Join-Path ([System.IO.Path]::GetTempPath()) 'formumind-smoke-stack.txt'
Set-Content -Path $sample -Encoding UTF8 -Value @(
    'Zinc phosphate is added to a waterborne epoxy primer as an anticorrosive pigment at 6 to 10 percent by weight.',
    'Neutral salt spray resistance of the cured film is reported after 500 hours according to ASTM B117.',
    'Adhesion to cold-rolled steel is tested by cross-cut after a 72 hour water immersion.'
)
$accepted = $null
$saved = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
try {
    $body = (& curl.exe -s -S --max-time 60 -F "file=@$sample;type=text/plain" "$Api/api/ingest" 2>&1 | Out-String)
    $accepted = $body | ConvertFrom-Json
} catch { Write-Host "upload: $body" } finally { $ErrorActionPreference = $saved }
Check 'the upload is accepted for background processing (HTTP 202 + task id)' ($accepted -and $accepted.task_id) $body

if ($accepted -and $accepted.task_id) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    $state = $null
    $first = Get-Date
    do {
        Start-Sleep -Milliseconds 500
        try { $state = Invoke-RestMethod -Uri "$Api/api/tasks/$($accepted.task_id)" -TimeoutSec 10 } catch { Write-Host "poll: $($_.Exception.Message)" }
    } until (($state -and $state.state -in @('completed', 'failed')) -or ((Get-Date) -gt $deadline))
    $took = ((Get-Date) - $first).TotalSeconds
    $detail = if ($state) { ($state | ConvertTo-Json -Compress -Depth 4) } else { 'no status' }
    Check "the worker finished the task ($([math]::Round($took, 1)) s)" ($state -and $state.state -eq 'completed') $detail
    Check 'the result carries the parsed evidence' ($state -and $state.result -and ([int]$state.result.total -ge 1)) $detail

    $workerLog = Join-Path $Logs 'celery.err.log'
    $received = (Test-Path $workerLog) -and [bool](Select-String -Path $workerLog -Pattern 'formumind\.file_ingest\[' -Quiet)
    Check 'it ran in the Celery worker, not in the API process (the worker log names the task)' $received "see $workerLog"
}

# ---------------------------------------------------------------- status

$status = Invoke-Runner -Action 'status'
Check 'status shows the API and Redis up' (($status.Text -match 'API\s+:8000\s+up') -and ($status.Text -match 'Redis\s+:6379\s+up'))
Check 'status names the worker pid it recorded' ($status.Text -match "worker pid $workerPid")

# ---------------------------------------------------------------- stop

if (-not $KeepRunning) {
    $stop = Invoke-Runner -Action 'stop'
    Check 'stop exits 0' ($stop.Code -eq 0)
    Check 'stop reports the worker it stopped' ($stop.Text -match 'stopped worker')
    foreach ($kind in @('api', 'worker')) {
        Check "the $kind pid file is gone" (-not (Test-Path (Join-Path $PidDir "$kind.pid")))
    }
    Start-Sleep -Seconds 3
    Check 'the API port is free again' (-not (Test-PortOpen -Port 8000))
    $left = Get-VenvProcesses
    Check 'no process of this venv is left (the worker used to be orphaned)' ($left.Count -eq 0) (($left | ForEach-Object { "$($_.ProcessId): $($_.CommandLine)" }) -join '; ')
    if ($left.Count -gt 0) { $left | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue } }
}

# ---------------------------------------------------------------- summary

Write-Host ''
if ($script:Failed.Count -eq 0) {
    Write-Host 'All checks passed.' -ForegroundColor Green
    exit 0
}
Write-Host ("{0} check(s) failed: {1}" -f $script:Failed.Count, ($script:Failed -join '; ')) -ForegroundColor Red
Write-Host "Logs: $Logs"
exit 1

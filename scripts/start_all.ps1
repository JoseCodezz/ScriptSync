<#
.SYNOPSIS
  Starts the whole ScriptSync stack on Windows with one command.
.DESCRIPTION
  Brand agents (from config/agents.json), the four impostors, the assistant (8080) and the web UI (5500).
  Presenter tools (impostor tests) are ON unless you pass -Product.
  Stale processes are the most common cause of odd results, so use -Restart when in doubt.
.EXAMPLE
  .\scripts\start_all.ps1 -Restart          # demo setup
  .\scripts\start_all.ps1 -Restart -Product # product view: no impostors
  .\scripts\stop_all.ps1                    # stop everything
#>
param(
    [switch]$Restart,
    [switch]$Product
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    throw "No .venv found. Run: python -m venv .venv ; .venv\Scripts\Activate.ps1 ; pip install -r requirements.txt"
}

$ports = 5500, 8080, 9001, 9002, 9101, 9102, 9103, 9104
$busy = Get-NetTCPConnection -State Listen -LocalPort $ports -ErrorAction SilentlyContinue
if ($busy -and -not $Restart) {
    Write-Host "Ports already in use: $(($busy.LocalPort | Sort-Object -Unique) -join ', ')" -ForegroundColor Yellow
    Write-Host "Something from an earlier run is still up. Re-run with -Restart to replace it." -ForegroundColor Yellow
    exit 1
}
if ($Restart) { & (Join-Path $PSScriptRoot "stop_all.ps1") }

if (-not (Test-Path (Join-Path $root ".env"))) {
    Write-Host "No .env found. Copy .env.example to .env (live DNS needs ANS_ALLOW_UNVALIDATED_DNS=1 until the registrar fixes the zone)." -ForegroundColor Yellow
}

# Presenter-only features (impostor tests) are enabled only when this is 1.
$env:SCRIPTSYNC_DEMO = $(if ($Product) { "0" } else { "1" })

$logDir = Join-Path $root "logs\run"
New-Item -ItemType Directory -Force $logDir | Out-Null

function Start-Svc([string]$name, [string]$argLine) {
    Start-Process -FilePath $py -ArgumentList $argLine -WorkingDirectory $root -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logDir "$name.out") `
        -RedirectStandardError (Join-Path $logDir "$name.err") | Out-Null
}

$cfg = (Get-Content (Join-Path $root "config\agents.json") -Raw | ConvertFrom-Json).agents
$services = @()   # name, port, health url (or $null for a plain TCP check)

foreach ($a in $cfg) {
    $port = ([uri]$a.endpoint).Port
    if ($a.role -eq "attacker") {
        Start-Svc "impostor-$($a.attack)" "-m dev.mock_agent --port $port --which A --mode $($a.attack) --name $($a.ansName)"
        $services += , @("impostor: $($a.attack)", $port, $null)
    } else {
        $domain = if ($a.ansName -match '\.v\d+\.\d+\.\d+\.(.+)$') { $Matches[1] } else { "scriptsync.health" }
        Start-Svc "agent-$($a.drug)" "-m brand_agent --label labels/$($a.drug).json --port $port --domain $domain"
        $services += , @("agent: $($a.drug)", $port, "http://127.0.0.1:$port/health")
    }
}
Start-Svc "assistant" "-m uvicorn assistant.server:app --port 8080"
$services += , @("assistant", 8080, "http://127.0.0.1:8080/health")
Start-Svc "web" "-m http.server 5500 --directory web"
$services += , @("web UI", 5500, "http://127.0.0.1:5500/")

# wait until everything answers (up to 40 s)
$deadline = (Get-Date).AddSeconds(40)
$state = @{}
while ((Get-Date) -lt $deadline -and $state.Count -lt $services.Count) {
    foreach ($s in $services) {
        if ($state.ContainsKey($s[0])) { continue }
        $up = $false
        try {
            if ($s[2]) { $up = ((Invoke-WebRequest -Uri $s[2] -UseBasicParsing -TimeoutSec 2).StatusCode -lt 500) }
            else { $up = [bool](Get-NetTCPConnection -State Listen -LocalPort $s[1] -ErrorAction SilentlyContinue) }
        } catch { $up = $false }
        if ($up) { $state[$s[0]] = $true }
    }
    Start-Sleep -Milliseconds 500
}

Write-Host ""
Write-Host "ScriptSync ($(if ($Product) { 'product' } else { 'demo' }) mode)" -ForegroundColor Cyan
foreach ($s in $services) {
    $ok = $state.ContainsKey($s[0])
    Write-Host ("  {0,-26} port {1,-5} {2}" -f $s[0], $s[1], $(if ($ok) { "up" } else { "DOWN  (see logs\run)" })) -ForegroundColor $(if ($ok) { "Green" } else { "Red" })
}

# Do the real agents actually pass identity verification right now?
try {
    $agents = Invoke-RestMethod -Uri "http://127.0.0.1:8080/agents" -TimeoutSec 60
    Write-Host ""
    Write-Host "Identity verification of the real agents:" -ForegroundColor Cyan
    foreach ($a in ($agents | Where-Object { $_.role -ne "attacker" })) {
        $bad = $a.verification.checks | Where-Object { -not $_.pass } | Select-Object -First 1
        if ($a.verification.ok) { Write-Host ("  {0,-16} verified ({1})" -f $a.drug, $a.verification.mode) -ForegroundColor Green }
        else { Write-Host ("  {0,-16} NOT VERIFIED: {1}" -f $a.drug, $bad.message) -ForegroundColor Red }
    }
    $failing = @($agents | Where-Object { $_.role -ne "attacker" -and -not $_.verification.ok })
    $msgs = ($failing | ForEach-Object { $_.verification.checks } | Where-Object { -not $_.pass } | ForEach-Object { $_.message }) -join " | "
    if ($msgs -match "does not publish") {
        Write-Host "  The agents' private keys (keys\*.ed25519) do not match the keys published in DNS." -ForegroundColor Yellow
        Write-Host "  Copy the keys\ folder from the machine that published the records (never through git), then re-run with -Restart." -ForegroundColor Yellow
    } elseif ($msgs -match "lookup failed|No domain record|No agent key") {
        Write-Host "  DNS lookup failed: check .env (ANS_ALLOW_UNVALIDATED_DNS=1) and that you are online." -ForegroundColor Yellow
    } elseif ($failing.Count -gt 0) {
        Write-Host "  See logs\run\assistant.err and the messages above." -ForegroundColor Yellow
    }
} catch { Write-Host "Could not query the assistant: $($_.Exception.Message)" -ForegroundColor Red }

Write-Host ""
Write-Host "Open http://localhost:5500      (add ?demo=1 for the presenter guide)"
Write-Host "Stop with .\scripts\stop_all.ps1"

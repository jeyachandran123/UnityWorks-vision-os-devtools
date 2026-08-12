<#
.SYNOPSIS
    Start the Vision OS platform service (the harness).

.DESCRIPTION
    Run this in its own terminal window and leave it open.

    Both the Validation Console (:5273) and the Demonstration App (:5280) are
    viewers — they hold no data and compute nothing. With this service down,
    Vite's dev proxy answers every /api call with a bare 500 and an empty body,
    which looks like an application fault but is a missing upstream.

.PARAMETER Static
    Bind the constant-answer understander instead of Qwen. Starts in seconds
    instead of minutes; attributes become constants and the UI says so.

.PARAMETER ServeFrames
    Allow decoded frames over HTTP. Off by default — pixels stay local (V12).

.EXAMPLE
    .\start-platform.ps1
    .\start-platform.ps1 -Static
    .\start-platform.ps1 -ServeFrames
#>
param(
    [switch]$Static,
    [switch]$ServeFrames,
    [int]$Port = 8808
)

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path (Split-Path -Parent $here) 'backend\venv\Scripts\python.exe'

if (-not (Test-Path $python)) {
    Write-Host "Python venv not found at $python" -ForegroundColor Red
    Write-Host "Expected the backend virtual environment alongside this repository."
    exit 1
}

# Refuse to start on a port that is already serving. Two harnesses on one port
# is a confusing failure: one binds, the other dies, and the console cannot tell
# which one it is talking to.
$busy = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
        Where-Object { $_.LocalPort -eq $Port }
if ($busy) {
    $owner = (Get-Process -Id $busy[0].OwningProcess -ErrorAction SilentlyContinue).ProcessName
    Write-Host "Port $Port is already in use by '$owner' (pid $($busy[0].OwningProcess))." -ForegroundColor Yellow
    Write-Host "If that is an old harness, stop it first:  Stop-Process -Id $($busy[0].OwningProcess)"
    exit 1
}

$env:VOSVC_PORT = "$Port"
if ($Static)      { $env:VOSVC_UNDERSTANDER = 'static' } else { Remove-Item Env:\VOSVC_UNDERSTANDER -ErrorAction SilentlyContinue }
if ($ServeFrames) { $env:VOSVC_SERVE_FRAMES = '1' }

Write-Host ''
Write-Host '  Vision OS Platform Service' -ForegroundColor Cyan
Write-Host "  http://127.0.0.1:$Port/api/v1/health"
Write-Host ''

if ($Static) {
    Write-Host '  understander : attr.static_head (forced) — attributes are constants' -ForegroundColor Yellow
} else {
    Write-Host '  understander : Qwen2.5-VL via local Ollama' -ForegroundColor Green
    Write-Host ''
    Write-Host '  First session takes several minutes. The P15 conformance gate makes real' -ForegroundColor DarkGray
    Write-Host '  inference calls to prove the adapter never fabricates, and CPU inference is' -ForegroundColor DarkGray
    Write-Host '  ~13s per call. Use -Static for a fast start without a model.' -ForegroundColor DarkGray

    try {
        $tags = Invoke-RestMethod 'http://127.0.0.1:11434/api/tags' -TimeoutSec 5
        $names = $tags.models | ForEach-Object { $_.name }
        if ($names -notcontains 'qwen2.5vl:7b') {
            Write-Host ''
            Write-Host "  Ollama is up but 'qwen2.5vl:7b' is not installed." -ForegroundColor Yellow
            Write-Host '  The harness will fall back to the static head and say so.' -ForegroundColor Yellow
        }
    } catch {
        Write-Host ''
        Write-Host '  Ollama is not reachable on 11434 — the harness will fall back to the' -ForegroundColor Yellow
        Write-Host '  static head and report the reason. Start it with: ollama serve' -ForegroundColor Yellow
    }
}

Write-Host ''
Write-Host '  Leave this window open. Closing it stops the service and every API' -ForegroundColor DarkGray
Write-Host '  call from the console will return 500.' -ForegroundColor DarkGray
Write-Host ''

Set-Location (Join-Path $here 'harness')
& $python -m vosvc_harness

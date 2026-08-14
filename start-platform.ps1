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

# --- the demo's use case, as configuration -------------------------------------
#
# Every line below names a DOCUMENT. Not one attribute, value, class or business
# rule appears in this script, in the harness, or in Vision OS — adding a use
# case is a file, and this is where a deployment says which files are live.
#
# Set only when unset, so an operator who exported their own choice keeps it.
$backend = Join-Path (Split-Path -Parent $here) 'backend'

if (-not $env:VISION_SEMANTIC_POLICY) {
    # Two policies, comma-separated. They have different subjects: kitchen PPE is
    # asked about people, object corroboration about the small objects a
    # closed-set detector is least able to name. One document cannot hold both
    # without demanding a head covering of a toothbrush.
    $env:VISION_SEMANTIC_POLICY = @(
        (Join-Path $backend 'config\policies\kitchen-safety.example.json'),
        (Join-Path $backend 'config\policies\object-identity.example.json')
    ) -join ','
}

# When corroboration is worth a model call. Absent this the trigger policy runs
# unwrapped and no verification happens at all — a supported configuration.
if (-not $env:VISION_VERIFICATION_RULES) {
    $env:VISION_VERIFICATION_RULES = Join-Path $backend 'config\policies\verification.example.json'
}

# The business requirements. Read by the compliance layer, never by Vision OS.
if (-not $env:COMPLIANCE_RULES) {
    $env:COMPLIANCE_RULES = Join-Path $backend 'config\rules\site-safety.example.json'
}

# A real model, not the constant head. `-Static` overrides this deliberately.
if (-not $Static -and -not $env:VISION_UNDERSTANDER_PROVIDER) {
    $env:VISION_UNDERSTANDER_PROVIDER = 'nvidia'
}

# Evidence retrieval, enabled for the demo as the deployment decision it is.
# A finding carries a crop reference; resolving one still needs a declared
# purpose and is still separately authorized, and this does not change that.
if (-not $env:VOSVC_ALLOW_EVIDENCE) { $env:VOSVC_ALLOW_EVIDENCE = '1' }

# Whole frames too, so the Frame-by-Frame picture renders instead of a black
# panel. Without this `frameUrl` is null and the card falls back to boxes on an
# empty background.
if (-not $env:VOSVC_SERVE_FRAMES) { $env:VOSVC_SERVE_FRAMES = '1' }

Write-Host ''
Write-Host '  Vision OS Platform Service' -ForegroundColor Cyan
Write-Host "  http://127.0.0.1:$Port/api/v1/health"
Write-Host ''

Write-Host "  policies     : $($env:VISION_SEMANTIC_POLICY -split ',' | ForEach-Object { Split-Path $_ -Leaf })" -ForegroundColor DarkGray
Write-Host "  verification : $(Split-Path $env:VISION_VERIFICATION_RULES -Leaf)" -ForegroundColor DarkGray
Write-Host "  rules        : $(Split-Path $env:COMPLIANCE_RULES -Leaf)" -ForegroundColor DarkGray
Write-Host ''

if ($Static) {
    Write-Host '  understander : attr.static_head (forced) — attributes are constants' -ForegroundColor Yellow
    Write-Host '  Compliance findings from constants are meaningless. Do not demo this.' -ForegroundColor Yellow
} elseif ($env:VISION_UNDERSTANDER_PROVIDER -eq 'ollama') {
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
            Write-Host '  The session will FAIL rather than answer with constants.' -ForegroundColor Yellow
        }
    } catch {
        Write-Host ''
        Write-Host '  Ollama is not reachable on 11434 — the session will FAIL rather than' -ForegroundColor Yellow
        Write-Host '  answer with constants. Start it with: ollama serve' -ForegroundColor Yellow
    }
} else {
    Write-Host "  understander : $($env:VISION_UNDERSTANDER_PROVIDER) (real model)" -ForegroundColor Green
    Write-Host ''
    Write-Host '  Crops leave this host — the composition root says so at boot, and the' -ForegroundColor DarkGray
    Write-Host '  model panel names whichever adapter actually answered.' -ForegroundColor DarkGray
    Write-Host ''
    Write-Host '  If the provider cannot be reached the session FAILS. It does not fall' -ForegroundColor DarkGray
    Write-Host '  back to constants: a compliance rule evaluating a fixed value would' -ForegroundColor DarkGray
    Write-Host '  report violations that nothing observed.' -ForegroundColor DarkGray
}

Write-Host ''
Write-Host '  Leave this window open. Closing it stops the service and every API' -ForegroundColor DarkGray
Write-Host '  call from the console will return 500.' -ForegroundColor DarkGray
Write-Host ''

Set-Location (Join-Path $here 'harness')
& $python -m vosvc_harness

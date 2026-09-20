[CmdletBinding()]
param(
    [switch]$SkipSystem
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$venv = Join-Path $repoRoot ".venv"
$python = Join-Path $venv "Scripts\python.exe"
$agentReach = Join-Path $venv "Scripts\agent-reach.exe"

if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    throw "Python Launcher (py) is required. Install Python 3.10+ first."
}
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    throw "Node.js 18+ is required for OpenCLI. Install Node.js first."
}

if (-not (Test-Path $python)) {
    py -3 -m venv $venv
}

& $python -m pip install -r (Join-Path $repoRoot "requirements.txt")
& $python -m pip install "https://github.com/Panniantong/agent-reach/archive/main.zip"

if ($SkipSystem) {
    & $agentReach install --env=auto
} else {
    & $agentReach install --env=auto --system --channels=opencli,twitter,xiaohongshu
}

Write-Host ""
Write-Host "Installed. Run:"
Write-Host "  .\.venv\Scripts\python.exe scripts\research_trip.py --help"
Write-Host "  .\.venv\Scripts\agent-reach.exe doctor --json"
Write-Host ""
Write-Host "Keep Chrome open and logged in for XiaoHongShu/OpenCLI sources."

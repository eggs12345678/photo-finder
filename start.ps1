param(
    [int]$Port = 8642,
    [switch]$NoBuild
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "uv is required: https://docs.astral.sh/uv/"
}

if (-not (Test-Path (Join-Path $root "server\.venv"))) {
    Write-Host "Installing Python dependencies..." -ForegroundColor Cyan
    Push-Location (Join-Path $root "server")
    uv sync --python 3.11
    Pop-Location
}

$dist = Join-Path $root "web\dist\index.html"
if (-not $NoBuild -and -not (Test-Path $dist)) {
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
        throw "Node.js/npm is required to build the UI (or pass -NoBuild to run the API only)."
    }
    Write-Host "Building the web UI..." -ForegroundColor Cyan
    Push-Location (Join-Path $root "web")
    if (-not (Test-Path "node_modules")) { npm install --no-audit --no-fund }
    npm run build
    Pop-Location
}

Push-Location (Join-Path $root "server")
try {
    uv run python -m photofinder serve --port $Port
}
finally {
    Pop-Location
}

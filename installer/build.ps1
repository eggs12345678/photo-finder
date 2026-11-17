# Build the Photo Finder Windows installer.
#
#   .\installer\build.ps1                 full build -> installer\dist
#   .\installer\build.ps1 -SkipDeps       reuse the previous dependency install
#   .\installer\build.ps1 -SkipWeb        reuse web\dist
#
# Requirements: uv, npm, Inno Setup 6, .NET Framework csc (ships with Windows).

[CmdletBinding()]
param(
    [switch]$SkipWeb,
    [switch]$SkipDeps,
    [switch]$SkipCompile,
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
$installerDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $installerDir
$build = Join-Path $installerDir "build"
$payload = Join-Path $build "payload"
$dist = Join-Path $installerDir "dist"

# Native tools (uv, npm, robocopy) write progress to stderr; Windows
# PowerShell treats that as a terminating error under "Stop". Run them with
# their own error preference and always decide on the exit code.
function Invoke-Native {
    param([string]$Command, [string[]]$Arguments = @())
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $Command @Arguments 2>&1 | ForEach-Object { Write-Host $_ }
    } finally {
        $ErrorActionPreference = $previous
    }
    return $LASTEXITCODE
}

function Require-Command([string]$Name, [string]$Hint) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "$Name is required. $Hint"
    }
}

function Find-Iscc {
    $candidates = @()
    if ($env:ISCC) { $candidates += $env:ISCC }
    $candidates += @(
        (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"),
        "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        "C:\Program Files\Inno Setup 6\ISCC.exe"
    )
    foreach ($path in $candidates) {
        if ($path -and (Test-Path $path)) { return $path }
    }
    throw "Inno Setup 6 is required (winget install JRSoftware.InnoSetup)."
}

function Find-PythonRuntime {
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $listed = & uv python list --only-installed --python-preference only-managed 2>$null
    } finally {
        $ErrorActionPreference = $previous
    }
    $match = $listed | Select-String -Pattern "(3\.11\.\d+).*(cpython-3\.11[^\s]*windows[^\s]*)" | Select-Object -First 1
    if ($match) {
        $path = $match.Matches[0].Groups[2].Value
        $candidate = Join-Path (Join-Path (& uv python dir) $path) "python.exe"
        if (Test-Path $candidate) { return (Split-Path -Parent $candidate) }
    }
    $dir = Join-Path (& uv python dir) "cpython-3.11.18-windows-x86_64-none"
    if (Test-Path (Join-Path $dir "python.exe")) { return $dir }
    throw "No managed Python 3.11 found. Run: uv python install 3.11"
}

Require-Command uv "See https://docs.astral.sh/uv/"
$iscc = Find-Iscc
$runtime = Find-PythonRuntime
Write-Host "runtime : $runtime"
Write-Host "iscc    : $iscc"

$version = (Select-String -Path (Join-Path $root "server\pyproject.toml") -Pattern '^version = "(.*)"').Matches[0].Groups[1].Value
if (-not $version) { throw "Could not read the version from server\pyproject.toml" }
Write-Host "version : $version"

if ($Clean -and (Test-Path $build)) { Remove-Item $build -Recurse -Force }
New-Item -ItemType Directory -Force -Path $build, $payload, $dist | Out-Null

# 1. Web UI ---------------------------------------------------------------
$webDist = Join-Path $root "web\dist"
if (-not $SkipWeb) {
    Require-Command npm "Install Node.js 20+."
    Push-Location (Join-Path $root "web")
    try {
        if (-not (Test-Path "node_modules")) {
            if ((Invoke-Native npm @("install", "--no-audit", "--no-fund")) -ne 0) { throw "npm install failed" }
        }
        if ((Invoke-Native npm @("run", "build")) -ne 0) { throw "npm run build failed" }
    } finally { Pop-Location }
    $webDist = Join-Path $root "web\dist"
}
if (-not (Test-Path (Join-Path $webDist "index.html"))) {
    throw "web\dist\index.html is missing. Run without -SkipWeb first."
}

# 2. Application files ----------------------------------------------------
Write-Host "Assembling payload ..."
$appDir = Join-Path $payload "app"
$webTarget = Join-Path $payload "web\dist"
foreach ($old in $appDir, (Join-Path $payload "web")) {
    if (Test-Path $old) { Remove-Item $old -Recurse -Force }
}
New-Item -ItemType Directory -Force -Path $appDir, $webTarget | Out-Null
Copy-Item (Join-Path $root "server\photofinder") (Join-Path $appDir "photofinder") -Recurse -Force
Copy-Item (Join-Path $installerDir "run.py") (Join-Path $appDir "run.py") -Force
Get-ChildItem $appDir -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force
Get-ChildItem $appDir -Recurse -File -Include *.pyc,*.pyo | Remove-Item -Force
Copy-Item (Join-Path $webDist "*") $webTarget -Recurse -Force

# 3. Python runtime -------------------------------------------------------
if (-not $SkipDeps -or -not (Test-Path (Join-Path $payload "python\python.exe"))) {
    Write-Host "Copying Python runtime ..."
    if (Test-Path (Join-Path $payload "python")) { Remove-Item (Join-Path $payload "python") -Recurse -Force }
    $code = Invoke-Native robocopy @($runtime, (Join-Path $payload "python"), "/MIR", "/NFL", "/NDL", "/NJH", "/NJS", "/XD", "__pycache__", "/XF", "*.pdb")
    if ($code -ge 8) { throw "robocopy failed with exit code $code" }
}

# 4. Dependencies ---------------------------------------------------------
$requirements = Join-Path $build "requirements.txt"
if (-not $SkipDeps) {
    Write-Host "Exporting pinned dependencies ..."
    Push-Location (Join-Path $root "server")
    try {
        $code = Invoke-Native uv @("export", "--quiet", "--no-dev", "--no-hashes", "--format", "requirements-txt", "--extra", "ocr-windows", "--extra", "desktop", "--output-file", $requirements)
        if ($code -ne 0) { throw "uv export failed" }
    } finally { Pop-Location }

    Write-Host "Installing dependencies into the payload (this can take a few minutes) ..."
    $code = Invoke-Native uv @("pip", "install", "--python", (Join-Path $payload "python\python.exe"), "--requirement", $requirements, "--link-mode", "copy", "--break-system-packages")
    if ($code -ne 0) { throw "uv pip install failed" }
} elseif (-not (Test-Path $requirements)) {
    throw "requirements.txt is missing; run without -SkipDeps once."
}

# 5. Icon and launcher ----------------------------------------------------
& (Join-Path $payload "python\python.exe") (Join-Path $installerDir "make_icon.py") (Join-Path $build "assets\icon.ico")
if ($LASTEXITCODE -ne 0) { throw "Icon generation failed" }
New-Item -ItemType Directory -Force -Path (Join-Path $payload "assets") | Out-Null
Copy-Item (Join-Path $build "assets\icon.ico") (Join-Path $payload "assets\icon.ico") -Force

if (-not $SkipCompile) {
    Write-Host "Compiling PhotoFinder.exe ..."
    $csc = Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\csc.exe"
    if (-not (Test-Path $csc)) { throw "csc.exe not found (install .NET Framework 4.x)." }
    $code = Invoke-Native $csc @(
        "/nologo",
        "/target:winexe",
        "/platform:anycpu",
        "/optimize+",
        "/win32icon:$(Join-Path $build 'assets\icon.ico')",
        "/out:$(Join-Path $payload 'PhotoFinder.exe')",
        "/reference:System.Windows.Forms.dll",
        (Join-Path $installerDir "Launcher.cs")
    )
    if ($code -ne 0) { throw "Launcher compilation failed" }
}

# 6. Installer ------------------------------------------------------------
Write-Host "Compiling installer ..."
Push-Location $installerDir
try {
    $code = Invoke-Native $iscc @("/Q", "/DAppVersion=$version", "photofinder.iss")
    if ($code -ne 0) { throw "Inno Setup compilation failed" }
} finally { Pop-Location }

$setup = Join-Path $dist "PhotoFinder-$version-setup.exe"
if (-not (Test-Path $setup)) { throw "Setup was not produced at $setup" }
$sizeMb = [math]::Round((Get-Item $setup).Length / 1MB, 1)
Write-Host ""
Write-Host "Installer ready: $setup ($sizeMb MB)" -ForegroundColor Green

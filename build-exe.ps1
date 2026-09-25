param(
  [string]$Version = "4.3",
  [string]$Name = "",
  [string]$IconPath = "",
  [switch]$Clean,
  [switch]$RecreateVenv
)

$ErrorActionPreference = "Stop"

$repoRoot = $PSScriptRoot
$backendDir = Join-Path $repoRoot "backend"
$frontendDir = Join-Path $repoRoot "frontend"
$journalFrontendDir = Join-Path $repoRoot "journal-frontend"
$backendVenv = Join-Path $backendDir ".venv"
$backendPython = Join-Path $backendVenv "Scripts\python.exe"
$frontendDist = Join-Path $frontendDir "dist"
$journalFrontendDist = Join-Path $journalFrontendDir "dist"
$backendDist = Join-Path $backendDir "dist"
$backendBuild = Join-Path $backendDir "build"
$repoDist = Join-Path $repoRoot "dist"
$resolvedIconPath = ""
$defaultIconPath = Join-Path $repoRoot "assets\\finaltrade.ico"

# Optional runtime deps — keep out of the EXE (sync via separate scripts / local cache).
$excludeModules = @(
  "akshare",
  "baostock",
  "pyarrow",
  "lxml",
  "aiohttp",
  "google",
  "google.auth",
  "cryptography",
  "matplotlib",
  "tqdm",
  "paramiko",
  "werkzeug",
  "html5lib",
  "fsspec",
  "openpyxl"
)

$forbiddenPipPackages = @(
  "akshare",
  "baostock",
  "pyarrow"
)

if (-not $Name -or $Name.Trim().Length -le 0) {
  $normalizedVersion = $Version.Trim()
  if (-not $normalizedVersion) {
    $normalizedVersion = "1.5"
  }
  $Name = "FinalTrade-V$normalizedVersion"
}

$backendSpec = Join-Path $backendDir ("{0}.spec" -f $Name)

if ($IconPath -and $IconPath.Trim().Length -gt 0) {
  if (-not (Test-Path $IconPath)) {
    throw "Icon file not found: $IconPath"
  }
  $resolvedIconPath = (Resolve-Path $IconPath).Path
}
elseif (Test-Path $defaultIconPath) {
  $resolvedIconPath = (Resolve-Path $defaultIconPath).Path
}

if ($RecreateVenv -and (Test-Path $backendVenv)) {
  Write-Host "Recreating backend virtual environment (-RecreateVenv)..."
  Remove-Item $backendVenv -Recurse -Force
}

if (-not (Test-Path $backendPython)) {
  Write-Host "Creating backend virtual environment..."
  python -m venv $backendVenv
}

Write-Host "Installing backend dependencies..."
& $backendPython -m pip install --upgrade pip
& $backendPython -m pip install -r (Join-Path $backendDir "requirements.txt")
& $backendPython -m pip install pyinstaller

$installedNames = @(
  & $backendPython -m pip list --format=json | ConvertFrom-Json | ForEach-Object { $_.name.ToLower() }
)
foreach ($pkg in $forbiddenPipPackages) {
  if ($installedNames -contains $pkg.ToLower()) {
    throw "Build venv must not include '$pkg'. Run with -RecreateVenv or: pip uninstall $pkg"
  }
}

Write-Host "Building frontend..."
Push-Location $frontendDir
try {
  if (-not (Test-Path (Join-Path $frontendDir "node_modules"))) {
    npm install
  }
  npm run build
}
finally {
  Pop-Location
}

if (-not (Test-Path (Join-Path $frontendDist "index.html"))) {
  throw "Frontend build failed: index.html not found in $frontendDist"
}

Write-Host "Building journal frontend..."
Push-Location $journalFrontendDir
try {
  if (-not (Test-Path (Join-Path $journalFrontendDir "node_modules"))) {
    npm install
  }
  npm run build
}
finally {
  Pop-Location
}
if (-not (Test-Path (Join-Path $journalFrontendDist "index.html"))) {
  throw "Journal frontend build failed: index.html not found in $journalFrontendDist"
}

if ($Clean) {
  Remove-Item (Join-Path $backendDist ("{0}.exe" -f $Name)) -Force -ErrorAction SilentlyContinue
  Remove-Item (Join-Path $backendBuild $Name) -Recurse -Force -ErrorAction SilentlyContinue
  Remove-Item $backendSpec -Force -ErrorAction SilentlyContinue
  Remove-Item (Join-Path $repoDist ("{0}.exe" -f $Name)) -Force -ErrorAction SilentlyContinue
}

Write-Host "Packaging exe with PyInstaller..."
$addData = "{0};frontend_dist" -f $frontendDist
$journalAddData = "{0};journal_frontend_dist" -f $journalFrontendDist
$pyiArgs = @(
  "--noconfirm",
  "--clean",
  "--onefile",
  "--name", $Name,
  "--add-data", $addData,
  "--add-data", $journalAddData,
  "--hidden-import", "uvicorn.loops.asyncio",
  "--hidden-import", "uvicorn.protocols.http.h11_impl",
  "--hidden-import", "uvicorn.lifespan.on",
  "desktop_launcher.py"
)

foreach ($mod in $excludeModules) {
  $pyiArgs += @("--exclude-module", $mod)
}

if ($resolvedIconPath) {
  $pyiArgs += @("--icon", $resolvedIconPath)
}

Push-Location $backendDir
try {
  & $backendPython -m PyInstaller @pyiArgs
}
finally {
  Pop-Location
}

New-Item -ItemType Directory -Path $repoDist -Force | Out-Null
$exePath = Join-Path $backendDist ("{0}.exe" -f $Name)
if (-not (Test-Path $exePath)) {
  throw "Packaging failed: $exePath not found"
}

$targetPath = Join-Path $repoDist ("{0}.exe" -f $Name)
Copy-Item -Path $exePath -Destination $targetPath -Force

$sizeMb = [math]::Round((Get-Item $targetPath).Length / 1MB, 2)
Write-Host ""
Write-Host "Build complete."
Write-Host "Output: $targetPath"
Write-Host "Size:   ${sizeMb} MB"

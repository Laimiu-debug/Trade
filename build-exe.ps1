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

# Bundle configured providers and their runtime dependencies.
$excludeModules = @(
  "pyarrow",
  "matplotlib",
  "torch",
  "tensorflow",
  "IPython",
  "notebook",
  "pytest"
)

function Invoke-BuildCommand {
  param([string]$FilePath, [string[]]$Arguments)
  & $FilePath @Arguments
  if ($LASTEXITCODE -ne 0) { throw "Build command failed ($LASTEXITCODE): $FilePath" }
}

function Remove-BuildArtifact {
  param([string]$Path, [string]$AllowedRoot)
  $absolute = [IO.Path]::GetFullPath($Path)
  $boundary = [IO.Path]::GetFullPath($AllowedRoot).TrimEnd('\') + '\'
  if (-not $absolute.StartsWith($boundary, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Build cleanup escaped its expected directory: $absolute"
  }
  if (Test-Path -LiteralPath $absolute) { Remove-Item -LiteralPath $absolute -Recurse -Force }
}

if (-not $Name -or $Name.Trim().Length -le 0) {
  $normalizedVersion = $Version.Trim()
  if (-not $normalizedVersion) {
    $normalizedVersion = "1.5"
  }
  $Name = "FinalTrade-V$normalizedVersion"
}

if ($Name -notmatch '^[A-Za-z0-9][A-Za-z0-9._ -]{0,79}$') {
  throw 'Name must be a simple artifact name without directory separators.'
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
  Remove-BuildArtifact $backendVenv $backendDir
}

if (-not (Test-Path $backendPython)) {
  Write-Host "Creating backend virtual environment..."
  Invoke-BuildCommand 'python' @('-m', 'venv', $backendVenv)
}

Write-Host "Installing backend dependencies..."
Invoke-BuildCommand $backendPython @('-m', 'pip', 'install', '--upgrade', 'pip')
Invoke-BuildCommand $backendPython @('-m', 'pip', 'install', '-r', (Join-Path $backendDir 'requirements.txt'))
Invoke-BuildCommand $backendPython @('-m', 'pip', 'install', 'pyinstaller==6.19.0')

Write-Host "Building frontend..."
Push-Location $frontendDir
try {
  Invoke-BuildCommand 'npm.cmd' @('ci')
  Invoke-BuildCommand 'npm.cmd' @('run', 'build')
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
  Invoke-BuildCommand 'npm.cmd' @('ci')
  Invoke-BuildCommand 'npm.cmd' @('run', 'build')
}
finally {
  Pop-Location
}
if (-not (Test-Path (Join-Path $journalFrontendDist "index.html"))) {
  throw "Journal frontend build failed: index.html not found in $journalFrontendDist"
}

if ($Clean) {
  Remove-BuildArtifact (Join-Path $backendDist ("{0}.exe" -f $Name)) $backendDist
  Remove-BuildArtifact (Join-Path $backendBuild $Name) $backendBuild
  Remove-BuildArtifact $backendSpec $backendDir
  Remove-BuildArtifact (Join-Path $repoDist ("{0}.exe" -f $Name)) $repoDist
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
  Invoke-BuildCommand $backendPython (@('-m', 'PyInstaller') + $pyiArgs)
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

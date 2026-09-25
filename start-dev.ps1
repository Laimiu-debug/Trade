param(
  [string]$FrontendUrl = 'http://127.0.0.1:4173',
  [string]$BackendUrl = 'http://127.0.0.1:8010',
  [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'

$repoRoot = $PSScriptRoot
$backendDir = Join-Path $repoRoot 'backend'
$frontendDir = Join-Path $repoRoot 'frontend'
$journalFrontendDir = Join-Path $repoRoot 'journal-frontend'
$logDir = Join-Path $repoRoot 'runtime-logs'
$backendPort = 8010
try {
  $backendUri = [System.Uri]$BackendUrl
  if ($backendUri.Port -gt 0) {
    $backendPort = $backendUri.Port
  }
}
catch {
  $backendPort = 8010
}

function Get-ListeningPids {
  param([int]$Port)

  $resultPids = @()
  try {
    $resultPids = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction Stop |
      Select-Object -ExpandProperty OwningProcess -Unique
  }
  catch {
    $lines = netstat -ano | Select-String ":$Port" | Select-String 'LISTENING'
    foreach ($line in $lines) {
      $parts = ($line.ToString() -split '\s+') | Where-Object { $_ -ne '' }
      if ($parts.Length -gt 0) {
        $value = 0
        if ([int]::TryParse($parts[-1], [ref]$value)) {
          $resultPids += $value
        }
      }
    }
  }

  return $resultPids | Where-Object { $_ -gt 0 } | Sort-Object -Unique
}

function Stop-PortProcesses {
  param([int]$Port)

  $portPids = Get-ListeningPids -Port $Port
  foreach ($procId in $portPids) {
    try {
      Stop-Process -Id $procId -Force -ErrorAction Stop
      Write-Host "Stopped PID $procId on port $Port"
    }
    catch {
      Write-Host ("Failed to stop PID {0} on port {1}: {2}" -f $procId, $Port, $_.Exception.Message)
    }
  }
}

function Stop-MatchingProcesses {
  param(
    [string]$ImageName,
    [string]$CommandPattern,
    [string]$Label
  )

  try {
    $procs = Get-CimInstance Win32_Process -Filter ("Name = '{0}'" -f $ImageName)
  }
  catch {
    return
  }

  foreach ($proc in $procs) {
    $cmd = [string]$proc.CommandLine
    if (-not $cmd) {
      continue
    }
    if ($cmd -notmatch $CommandPattern) {
      continue
    }
    try {
      Stop-Process -Id $proc.ProcessId -Force -ErrorAction Stop
      Write-Host ("Stopped {0} PID {1}" -f $Label, $proc.ProcessId)
    }
    catch {
      Write-Host ("Failed to stop {0} PID {1}: {2}" -f $Label, $proc.ProcessId, $_.Exception.Message)
    }
  }
}

function Wait-HttpReady {
  param(
    [string]$Url,
    [int]$TimeoutSec = 45
  )

  $deadline = (Get-Date).AddSeconds($TimeoutSec)
  while ((Get-Date) -lt $deadline) {
    try {
      $resp = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 4
      if ($resp.StatusCode -ge 200 -and $resp.StatusCode -lt 500) {
        return $true
      }
    }
    catch {
      # keep polling
    }
    Start-Sleep -Milliseconds 600
  }

  return $false
}

function Show-LogTail {
  param([string]$Path)

  if (Test-Path $Path) {
    Write-Host "---- $Path (tail) ----"
    Get-Content $Path -Tail 40
  }
}

function Resolve-LogPath {
  param([string]$DefaultPath)

  try {
    if (Test-Path $DefaultPath) {
      Clear-Content $DefaultPath -Force
    }
    return $DefaultPath
  }
  catch {
    $dir = Split-Path $DefaultPath -Parent
    $name = [System.IO.Path]::GetFileNameWithoutExtension($DefaultPath)
    $ext = [System.IO.Path]::GetExtension($DefaultPath)
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    return Join-Path $dir ("{0}.{1}{2}" -f $name, $stamp, $ext)
  }
}

function Move-LegacyLogsToLogDir {
  param([string]$RootDir, [string]$TargetDir)

  $patterns = @('backend-dev*.log', 'frontend-dev*.log')
  foreach ($pattern in $patterns) {
    $legacyFiles = Get-ChildItem -Path $RootDir -File -Filter $pattern -ErrorAction SilentlyContinue
    foreach ($file in $legacyFiles) {
      $targetPath = Join-Path $TargetDir $file.Name
      if (([string]$file.FullName).Equals([string]$targetPath, [System.StringComparison]::OrdinalIgnoreCase)) {
        continue
      }
      try {
        Move-Item -Path $file.FullName -Destination $targetPath -Force
      }
      catch {
        # ignore locked files and continue startup
      }
    }
  }
}

if (-not (Test-Path $backendDir)) {
  throw "Backend directory not found: $backendDir"
}
if (-not (Test-Path $frontendDir)) {
  throw "Frontend directory not found: $frontendDir"
}
if (-not (Test-Path $logDir)) {
  New-Item -ItemType Directory -Path $logDir -Force | Out-Null
}

Write-Host 'Starting Final Trade...'

Stop-MatchingProcesses -ImageName 'FinalTrade-V3.2.exe' -CommandPattern '.' -Label 'finaltrade-exe'
Stop-MatchingProcesses -ImageName 'FinalTrade-V3.1.exe' -CommandPattern '.' -Label 'finaltrade-exe'
Stop-MatchingProcesses -ImageName 'FinalTrade-V2.4.exe' -CommandPattern '.' -Label 'finaltrade-exe'
Stop-MatchingProcesses -ImageName 'python.exe' -CommandPattern 'uvicorn\\s+app\\.main:app' -Label 'backend'
Stop-MatchingProcesses -ImageName 'node.exe' -CommandPattern '(vite(\\.js)?\\s+--host\\s+127\\.0\\.0\\.1\\s+--port\\s+4173)|(npm\\s+run\\s+dev:host)' -Label 'frontend'
Stop-MatchingProcesses -ImageName 'cmd.exe' -CommandPattern 'npm\\s+run\\s+dev:host' -Label 'frontend-wrapper'

Stop-PortProcesses -Port $backendPort
# Also clear old default backend port to avoid stale process conflicts.
Stop-PortProcesses -Port 8000
Stop-PortProcesses -Port 4173

Move-LegacyLogsToLogDir -RootDir $repoRoot -TargetDir $logDir

$backendOut = Resolve-LogPath -DefaultPath (Join-Path $logDir 'backend-dev.out.log')
$backendErr = Resolve-LogPath -DefaultPath (Join-Path $logDir 'backend-dev.err.log')
$frontendOut = Resolve-LogPath -DefaultPath (Join-Path $logDir 'frontend-dev.out.log')
$frontendErr = Resolve-LogPath -DefaultPath (Join-Path $logDir 'frontend-dev.err.log')

$backendPython = Join-Path $backendDir '.venv\Scripts\python.exe'
if (-not (Test-Path $backendPython)) {
  $pythonCmd = Get-Command python -ErrorAction SilentlyContinue
  if (-not $pythonCmd) {
    throw 'Python not found. Create backend .venv or install python first.'
  }
  $backendPython = $pythonCmd.Source
}

$npmCmd = Get-Command npm.cmd -ErrorAction SilentlyContinue
if (-not $npmCmd) {
  $npmCmd = Get-Command npm -ErrorAction SilentlyContinue
}
if (-not $npmCmd) {
  throw 'npm not found. Please install Node.js first.'
}

Write-Host 'Building the journal frontend...'
Push-Location $journalFrontendDir
try {
  if (-not (Test-Path (Join-Path $journalFrontendDir 'node_modules'))) {
    & $npmCmd.Source ci
  }
  & $npmCmd.Source run build
  if ($LASTEXITCODE -ne 0) { throw 'Journal frontend build failed.' }
}
finally {
  Pop-Location
}

$env:TDX_TREND_BACKTEST_MATRIX_ENGINE = '1'
$backendProc = Start-Process -FilePath $backendPython -ArgumentList @('-m', 'uvicorn', 'app.main:app', '--app-dir', '.', '--reload', '--host', '127.0.0.1', '--port', "$backendPort") -WorkingDirectory $backendDir -RedirectStandardOutput $backendOut -RedirectStandardError $backendErr -PassThru
$frontendProc = Start-Process -FilePath 'cmd.exe' -ArgumentList @('/c', "set VITE_API_PROXY_TARGET=$BackendUrl && npm run dev:host") -WorkingDirectory $frontendDir -RedirectStandardOutput $frontendOut -RedirectStandardError $frontendErr -PassThru

Write-Host "Backend PID: $($backendProc.Id)"
Write-Host "Frontend PID: $($frontendProc.Id)"
Write-Host "Backend logs:  $backendOut | $backendErr"
Write-Host "Frontend logs: $frontendOut | $frontendErr"

if (-not (Wait-HttpReady -Url "$BackendUrl/health" -TimeoutSec 50)) {
  Show-LogTail -Path $backendErr
  Show-LogTail -Path $backendOut
  throw "Backend not ready: $BackendUrl/health"
}

if (-not (Wait-HttpReady -Url $FrontendUrl -TimeoutSec 70)) {
  Show-LogTail -Path $frontendErr
  Show-LogTail -Path $frontendOut
  throw "Frontend not ready: $FrontendUrl"
}

Write-Host ''
Write-Host 'Services are ready:'
Write-Host "- Frontend: $FrontendUrl"
Write-Host "- Backend : $BackendUrl"

if (-not $NoBrowser) {
  Start-Process $FrontendUrl
  Write-Host 'Browser opened.'
}







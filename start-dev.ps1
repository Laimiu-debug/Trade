param(
  [string]$FrontendUrl = 'http://127.0.0.1:4173',
  [string]$BackendUrl = 'http://127.0.0.1:8010',
  [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'
$repoRoot = $PSScriptRoot
$backendDir = Join-Path $repoRoot 'backend'
$frontendDir = Join-Path $repoRoot 'frontend'
$journalDir = Join-Path $repoRoot 'journal-frontend'
$logDir = Join-Path $repoRoot 'runtime-logs'

function Resolve-LocalUrl {
  param([string]$Value)
  $endpoint = $null
  if (-not [Uri]::TryCreate($Value, [UriKind]::Absolute, [ref]$endpoint) -or
      $endpoint.Scheme -ne 'http' -or $endpoint.Host -notin @('127.0.0.1', 'localhost') -or
      $endpoint.UserInfo -or $endpoint.Query -or $endpoint.Fragment -or
      $endpoint.AbsolutePath -ne '/' -or $endpoint.Port -lt 1) {
    throw 'Development URLs must use http://127.0.0.1:<port> without a path, credentials or query.'
  }
  return $endpoint
}

function Find-FreePort {
  param([int]$Preferred, [int]$Excluded = 0)
  foreach ($port in $Preferred..([Math]::Min(65535, $Preferred + 20))) {
    if ($port -eq $Excluded) { continue }
    $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, $port)
    try { $listener.Start(); return $port }
    catch [Net.Sockets.SocketException] { }
    finally { $listener.Stop() }
  }
  throw "No free local port near $Preferred. Existing services were left running."
}

function Invoke-Checked {
  param([string]$FilePath, [string[]]$Arguments)
  & $FilePath @Arguments
  if ($LASTEXITCODE -ne 0) { throw "Command failed ($LASTEXITCODE): $FilePath" }
}

function Wait-HttpReady {
  param([string]$Url, [Diagnostics.Process]$Process, [int]$TimeoutSec = 45)
  $deadline = (Get-Date).AddSeconds($TimeoutSec)
  while ((Get-Date) -lt $deadline) {
    if ($Process.HasExited) { return $false }
    try {
      $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3
      if ($response.StatusCode -eq 200) { return $true }
    } catch { }
    Start-Sleep -Milliseconds 300
  }
  return $false
}

$backendEndpoint = Resolve-LocalUrl $BackendUrl
$frontendEndpoint = Resolve-LocalUrl $FrontendUrl
$backendPort = Find-FreePort $backendEndpoint.Port
$frontendPort = Find-FreePort $frontendEndpoint.Port -Excluded $backendPort
$BackendUrl = "http://127.0.0.1:$backendPort"
$FrontendUrl = "http://127.0.0.1:$frontendPort"
$backendPython = Join-Path $backendDir '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $backendPython)) { $backendPython = (Get-Command python -ErrorAction Stop).Source }
$nodePath = (Get-Command node -ErrorAction Stop).Source
$npmPath = (Get-Command npm.cmd -ErrorAction Stop).Source
$nodeVersion = [version](& $nodePath -p 'process.versions.node')
if (-not (($nodeVersion.Major -eq 20 -and $nodeVersion.Minor -ge 19) -or
          ($nodeVersion.Major -eq 22 -and $nodeVersion.Minor -ge 12) -or $nodeVersion.Major -gt 22)) {
  throw 'Node.js ^20.19.0 or >=22.12.0 is required.'
}

foreach ($directory in @($frontendDir, $journalDir)) {
  Push-Location $directory
  try {
    if (-not (Test-Path -LiteralPath (Join-Path $directory 'node_modules'))) {
      Invoke-Checked $npmPath @('ci')
    }
    if ($directory -eq $journalDir) { Invoke-Checked $npmPath @('run', 'build') }
  } finally { Pop-Location }
}

New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
$backendOut = Join-Path $logDir "backend-dev.$stamp.out.log"
$backendErr = Join-Path $logDir "backend-dev.$stamp.err.log"
$frontendOut = Join-Path $logDir "frontend-dev.$stamp.out.log"
$frontendErr = Join-Path $logDir "frontend-dev.$stamp.err.log"
$backendProc = $null
$frontendProc = $null
$previousProxy = [Environment]::GetEnvironmentVariable('VITE_API_PROXY_TARGET')
$previousMatrix = [Environment]::GetEnvironmentVariable('TDX_TREND_BACKTEST_MATRIX_ENGINE')
$previousJournalOrigins = [Environment]::GetEnvironmentVariable('TRADING_MS_ALLOWED_ORIGINS')
try {
  $env:VITE_API_PROXY_TARGET = $BackendUrl
  $env:TDX_TREND_BACKTEST_MATRIX_ENGINE = '1'
  $env:TRADING_MS_ALLOWED_ORIGINS = (@($previousJournalOrigins, $FrontendUrl) | Where-Object { $_ }) -join ','
  $backendProc = Start-Process -FilePath $backendPython -ArgumentList @('-m', 'uvicorn', 'app.main:app', '--app-dir', '.', '--host', '127.0.0.1', '--port', "$backendPort") -WorkingDirectory $backendDir -WindowStyle Hidden -RedirectStandardOutput $backendOut -RedirectStandardError $backendErr -PassThru
  $viteScript = '"' + (Join-Path $frontendDir 'node_modules\vite\bin\vite.js') + '"'
  $frontendProc = Start-Process -FilePath $nodePath -ArgumentList @($viteScript, '--host', '127.0.0.1', '--port', "$frontendPort", '--strictPort') -WorkingDirectory $frontendDir -WindowStyle Hidden -RedirectStandardOutput $frontendOut -RedirectStandardError $frontendErr -PassThru
  if (-not (Wait-HttpReady "$BackendUrl/health" $backendProc)) { throw "Backend not ready. See $backendErr" }
  if (-not (Wait-HttpReady $FrontendUrl $frontendProc)) { throw "Frontend not ready. See $frontendErr" }
  Write-Host "Frontend: $FrontendUrl (PID $($frontendProc.Id))"
  Write-Host "Backend:  $BackendUrl (PID $($backendProc.Id))"
  Write-Host "Logs: $logDir"
  if (-not $NoBrowser) { Start-Process $FrontendUrl }
} catch {
  foreach ($ownedProcess in @($frontendProc, $backendProc)) {
    if ($null -ne $ownedProcess -and -not $ownedProcess.HasExited) {
      Stop-Process -InputObject $ownedProcess -Force -ErrorAction SilentlyContinue
    }
  }
  throw
} finally {
  [Environment]::SetEnvironmentVariable('VITE_API_PROXY_TARGET', $previousProxy)
  [Environment]::SetEnvironmentVariable('TDX_TREND_BACKTEST_MATRIX_ENGINE', $previousMatrix)
  [Environment]::SetEnvironmentVariable('TRADING_MS_ALLOWED_ORIGINS', $previousJournalOrigins)
}

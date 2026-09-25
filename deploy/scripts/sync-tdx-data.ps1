# ==============================================
# Final Trade - TDX Data Sync Script (Windows)
# Upload local TDX vipdoc data to Ubuntu server
#
# Modes:
#   Daily  - Only daily + aux data (~278 MB, 1-2 min)
#   Full   - Include minute data too (~6.8 GB, first time only)
# ==============================================

# ---------- Config ----------

$TDX_PATH = "E:\TDX\vipdoc"
$SERVER_USER = "ubuntu"
$SERVER_HOST = "43.142.188.252"
$SSH_KEY = "$env:USERPROFILE\.ssh\id_ed25519.txt"
$SERVER_TDX_DIR = "/home/ubuntu/final-trade/tdx-data"
$MARKETS = @("sh", "sz", "bj")

# ---------- Mode selection ----------

Write-Host "============================================" -ForegroundColor Cyan
Write-Host " Final Trade - TDX Data Sync" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  1) Daily sync (daily + aux, ~278 MB, fast)" -ForegroundColor White
Write-Host "  2) Full sync  (daily + minute + aux, ~6.8 GB)" -ForegroundColor White
Write-Host ""

$choice = Read-Host "Select mode [1/2] (default: 1)"
if ($choice -eq "2") {
    $SYNC_MINUTE = $true
    $mode = "FULL"
} else {
    $SYNC_MINUTE = $false
    $mode = "DAILY"
}

Write-Host ""
Write-Host "Mode: $mode (minute: $(if ($SYNC_MINUTE) {'ON'} else {'OFF'}))" -ForegroundColor Yellow

# ---------- Check TDX path ----------

if (-not (Test-Path $TDX_PATH)) {
    Write-Host "[ERROR] TDX path not found: $TDX_PATH" -ForegroundColor Red
    exit 1
}

# ---------- Test SSH ----------

Write-Host "[1/4] Testing SSH..." -ForegroundColor Yellow -NoNewline
$null = ssh -i $SSH_KEY -o ConnectTimeout=5 -o BatchMode=yes "${SERVER_USER}@${SERVER_HOST}" "echo ok" 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host " FAILED" -ForegroundColor Red
    exit 1
}
Write-Host " OK" -ForegroundColor Green

ssh -i $SSH_KEY "${SERVER_USER}@${SERVER_HOST}" "mkdir -p ${SERVER_TDX_DIR}/{sh/lday,sz/lday,bj/lday,sh/minline,sz/minline,bj/minline,T0002/hq_cache}"

# ---------- Package ----------

Write-Host "[2/4] Packaging..." -ForegroundColor Yellow

$tempDir = Join-Path $env:TEMP "final-trade-sync"
if (Test-Path $tempDir) { Remove-Item $tempDir -Recurse -Force }
New-Item -ItemType Directory -Path $tempDir | Out-Null

$totalFiles = 0
$totalSize = 0

foreach ($market in $MARKETS) {
    # Daily (.day) - always sync
    $src = Join-Path $TDX_PATH "$market\lday"
    if (Test-Path $src) {
        $dest = Join-Path $tempDir "$market\lday"
        New-Item -ItemType Directory -Path $dest -Force | Out-Null
        $files = Get-ChildItem "$src\*.day" -ErrorAction SilentlyContinue
        if ($files) {
            Copy-Item "$src\*.day" $dest
            $totalFiles += $files.Count
            $totalSize += ($files | Measure-Object -Property Length -Sum).Sum
            Write-Host "  $market daily: $($files.Count) files" -ForegroundColor White
        }
    }

    # Minute (.lc1) - only in full mode
    if ($SYNC_MINUTE) {
        $src = Join-Path $TDX_PATH "$market\minline"
        if (Test-Path $src) {
            $dest = Join-Path $tempDir "$market\minline"
            New-Item -ItemType Directory -Path $dest -Force | Out-Null
            $files = Get-ChildItem "$src\*.lc1" -ErrorAction SilentlyContinue
            if ($files) {
                Copy-Item "$src\*.lc1" $dest
                $totalFiles += $files.Count
                $totalSize += ($files | Measure-Object -Property Length -Sum).Sum
                Write-Host "  $market minute: $($files.Count) files" -ForegroundColor White
            }
        }
    }
}

# Auxiliary data (always)
$hqCachePath = Join-Path (Split-Path $TDX_PATH -Parent) "T0002\hq_cache"
if (Test-Path $hqCachePath) {
    $dest = Join-Path $tempDir "T0002\hq_cache"
    New-Item -ItemType Directory -Path $dest -Force | Out-Null
    foreach ($f in @("shs.tnf", "szs.tnf", "bjs.tnf", "base.dbf")) {
        $src = Join-Path $hqCachePath $f
        if (Test-Path $src) {
            Copy-Item $src $dest
            $totalFiles++
            $totalSize += (Get-Item $src).Length
            Write-Host "  aux: $f" -ForegroundColor White
        }
    }
}

$totalSizeMB = [math]::Round($totalSize / 1MB, 1)
Write-Host "  Total: $totalFiles files, $totalSizeMB MB" -ForegroundColor Green

# ---------- Compress ----------

Write-Host "[3/4] Compressing..." -ForegroundColor Yellow

$archivePath = Join-Path $env:TEMP "tdx-sync.tar.gz"
if (Test-Path $archivePath) { Remove-Item $archivePath -Force }

& "$env:SystemRoot\System32\tar.exe" -czf $archivePath -C $tempDir . 2>$null

$archiveSizeMB = [math]::Round((Get-Item $archivePath).Length / 1MB, 1)
Write-Host "  $totalSizeMB MB -> $archiveSizeMB MB (compressed)" -ForegroundColor Green

# ---------- Upload ----------

Write-Host "[4/4] Uploading $archiveSizeMB MB..." -ForegroundColor Yellow

# Upload with progress tracking using scp
scp -i $SSH_KEY $archivePath "${SERVER_USER}@${SERVER_HOST}:/tmp/tdx-sync.tar.gz"

Write-Host "  Extracting on server..." -ForegroundColor Yellow

$extractScript = "cd ${SERVER_TDX_DIR} && tar -xzf /tmp/tdx-sync.tar.gz && rm -f /tmp/tdx-sync.tar.gz"
ssh -i $SSH_KEY "${SERVER_USER}@${SERVER_HOST}" $extractScript

Write-Host "  Done" -ForegroundColor Green

# Cleanup local temp
Remove-Item $tempDir -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item $archivePath -Force -ErrorAction SilentlyContinue

# ---------- Verify ----------

Write-Host ""
Write-Host "Verifying..." -ForegroundColor Yellow
$verifyScript = "echo '  Daily:  ' && find ${SERVER_TDX_DIR} -name '*.day' | wc -l && echo '  Minute: ' && find ${SERVER_TDX_DIR} -name '*.lc1' | wc -l && echo '  Names:  ' && find ${SERVER_TDX_DIR} -name '*.tnf' | wc -l && echo '  Size:   ' && du -sh ${SERVER_TDX_DIR} | cut -f1"
$verify = ssh -i $SSH_KEY "${SERVER_USER}@${SERVER_HOST}" $verifyScript
Write-Host $verify -ForegroundColor White

Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host " Sync Complete! ($mode)" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  Files:  $totalFiles ($totalSizeMB MB -> $archiveSizeMB MB)" -ForegroundColor White
Write-Host "  Server: ${SERVER_TDX_DIR}" -ForegroundColor White
Write-Host ""

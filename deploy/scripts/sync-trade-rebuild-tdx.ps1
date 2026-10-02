<#
Explicit administrator tool. No hard-coded host, credentials, project, or TDX path.
Uploads a verified ZIP64 bundle and extracts into a NEW directory. Does not switch
TRADE_TDX_ROOT, restart a service, or touch existing server market data.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Source,
    [Parameter(Mandatory = $true)][string]$HostAlias,
    [Parameter(Mandatory = $true)][string]$RemoteProject,
    [Parameter(Mandatory = $true)][string]$RemoteTarget,
    [string]$LocalPython = 'python',
    [string]$RemotePython = 'python3',
    [switch]$IncludeMinute,
    [ValidateRange(1, 32)][int]$MaxTotalGiB = 16,
    [ValidateRange(1, 512)][int]$MaxFileMiB = 100,
    [ValidateRange(1, 100000)][int]$MaxFiles = 30000
)
$ErrorActionPreference = 'Stop'

function Quote-Remote([string]$Value) {
    return "'" + $Value.Replace("'", "'\''") + "'"
}
function Assert-RemotePath([string]$Value) {
    if ($Value -notmatch '^/[A-Za-z0-9_./ -]+$' -or $Value -match '(^|/)\.\.?(/|$)' -or $Value.EndsWith('/') -or $Value.Contains('//')) {
        throw 'Remote paths must be absolute POSIX paths without dot segments or shell characters.'
    }
}
function Invoke-Checked([string]$Tool, [string[]]$Arguments) {
    $output = & $Tool @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Tool failed with exit code $LASTEXITCODE" }
    return ($output -join "`n")
}

$remoteTemp = $null
$localTemp = $null
$bundle = $null
$failure = $null
$receipt = $null
$sshOptions = @('-o', 'StrictHostKeyChecking=yes', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10')
try {
    if ($HostAlias -notmatch '^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$') { throw 'Use a configured SSH Host alias, not a URL or command.' }
    Assert-RemotePath $RemoteProject
    Assert-RemotePath $RemoteTarget
    if ($RemotePython -notmatch '^/?[A-Za-z0-9_./-]+$' -or $RemotePython.StartsWith('-')) { throw 'Invalid remote Python executable.' }
    $cli = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..\scripts\trade_rebuild_tdx_bundle.py'))
    if (-not (Test-Path -LiteralPath $cli -PathType Leaf)) { throw 'Local standalone bundle CLI is missing.' }
    $localTemp = Join-Path ([IO.Path]::GetTempPath()) ('trade-rebuild-tdx-' + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $localTemp | Out-Null
    $bundle = Join-Path $localTemp 'bundle.zip'
    $budgets = @('--max-total-gib', "$MaxTotalGiB", '--max-file-mib', "$MaxFileMiB", '--max-files', "$MaxFiles")
    $createArgs = @($cli, 'create', '--source', $Source, '--output', $bundle) + $budgets
    if ($IncludeMinute) { $createArgs += '--include-minute' }
    $created = (Invoke-Checked $LocalPython $createArgs) | ConvertFrom-Json
    if ($created.ok -ne $true -or $created.result.archive_sha256 -notmatch '^[a-f0-9]{64}$') { throw 'Invalid local bundle receipt.' }
    $archiveHash = [string]$created.result.archive_sha256
    # mktemp creates a private directory; its returned path is validated before use.
    $remoteTemp = (Invoke-Checked 'ssh' ($sshOptions + @($HostAlias, 'umask 077; mktemp -d /tmp/trade-rebuild-tdx-XXXXXXXXXXXX'))).Trim()
    if ($remoteTemp -notmatch '^/tmp/trade-rebuild-tdx-[A-Za-z0-9]{8,32}$') { $remoteTemp = $null; throw 'Invalid remote temporary directory.' }
    $remoteArchive = "$remoteTemp/bundle.zip"
    $null = Invoke-Checked 'scp' ($sshOptions + @($bundle, "${HostAlias}:$remoteArchive"))
    $remoteCli = "$RemoteProject/scripts/trade_rebuild_tdx_bundle.py"
    # extract-new verifies all archive/file hashes before creating the new target.
    $command = (Quote-Remote $RemotePython) + ' ' + (Quote-Remote $remoteCli) + ' extract-new --archive ' + (Quote-Remote $remoteArchive) +
        ' --sha256 ' + (Quote-Remote $archiveHash) + ' --target ' + (Quote-Remote $RemoteTarget) +
        " --max-total-gib $MaxTotalGiB --max-file-mib $MaxFileMiB --max-files $MaxFiles"
    $receipt = (Invoke-Checked 'ssh' ($sshOptions + @($HostAlias, $command))) | ConvertFrom-Json
    if ($receipt.ok -ne $true -or $receipt.result.archive_sha256 -ne $archiveHash -or
        $receipt.result.total_files -ne $created.result.total_files -or $receipt.result.total_bytes -ne $created.result.total_bytes -or
        $receipt.result.manifest_sha256 -ne $created.result.manifest_sha256 -or
        $receipt.result.validation -ne 'transport_integrity_only' -or -not $receipt.result.completed_at -or
        $receipt.result.target -ne $RemoteTarget -or $receipt.result.root_switch -ne 'manual_only') {
        throw 'Remote extraction receipt does not match the uploaded bundle.'
    }
} catch {
    $failure = $_.Exception.Message
} finally {
    if ($remoteTemp) {
        try {
            # Only our two exact paths; do not recurse or remove existing data.
            $cleanup = 'rm -f -- ' + (Quote-Remote "$remoteTemp/bundle.zip") + ' && rmdir -- ' + (Quote-Remote $remoteTemp)
            $null = Invoke-Checked 'ssh' ($sshOptions + @($HostAlias, $cleanup))
        } catch {
            if (-not $failure) { $failure = 'Remote temporary-file cleanup failed: ' + $_.Exception.Message }
        }
    }
    if (-not $failure -and $localTemp) {
        # Exact paths created above; no recursive deletion or shell hand-off.
        if (Test-Path -LiteralPath $bundle) { Remove-Item -LiteralPath $bundle }
        Remove-Item -LiteralPath $localTemp
    }
}
if ($failure) {
    Write-Error "$failure. No data-root switch was performed. Local bundle (if created): $bundle" -ErrorAction Continue
    exit 1
}
Write-Output ($receipt | ConvertTo-Json -Depth 8 -Compress)
Write-Host 'Verified extraction completed. Administrator must inspect the receipt and explicitly select TRADE_TDX_ROOT, then restart through the normal launcher.'
exit 0

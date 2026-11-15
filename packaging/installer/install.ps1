param(
    [string]$PackageZip = "",
    [string]$Checksums = "",
    [string]$TargetRoot = "",
    [switch]$NoStart,
    [switch]$NoStopProcess,
    [switch]$NoPause
)

$ErrorActionPreference = "Stop"
$AppName = "DoubaoMouseBridge"

function Stop-WithError {
    param([string]$Message)
    Write-Host ""
    Write-Host "ERROR: $Message" -ForegroundColor Red
    Write-Host ""
    if (-not $NoPause) { Read-Host "Press Enter to close" | Out-Null }
    exit 1
}

function Pause-IfNeeded {
    if (-not $NoPause) {
        Write-Host ""
        Read-Host "Press Enter to close" | Out-Null
    }
}

if (-not $TargetRoot) {
    if (-not $env:LOCALAPPDATA) { Stop-WithError "LOCALAPPDATA is not set." }
    $TargetRoot = Join-Path $env:LOCALAPPDATA "Programs\$AppName"
}

if (-not $PackageZip) {
    $found = Get-ChildItem -LiteralPath $PSScriptRoot -Filter "DoubaoMouseBridge-v*-app.zip" -File -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    if (-not $found) { Stop-WithError "Cannot find DoubaoMouseBridge-v*-app.zip next to this script." }
    $PackageZip = $found.FullName
}

if (-not $Checksums) { $Checksums = Join-Path $PSScriptRoot "checksums.txt" }

if (-not (Test-Path -LiteralPath $PackageZip)) { Stop-WithError "Package not found: $PackageZip" }
if (-not (Test-Path -LiteralPath $Checksums)) { Stop-WithError "Checksum file not found: $Checksums" }

$expected = @{}
foreach ($line in Get-Content -LiteralPath $Checksums) {
    if ($line -match '^\s*([0-9a-fA-F]{64})\s+\*?(.+?)\s*$') {
        $expected[$matches[2]] = $matches[1].ToUpperInvariant()
    }
}

$zipName = Split-Path -Leaf $PackageZip
if (-not $expected.ContainsKey($zipName)) { Stop-WithError "No checksum recorded for $zipName." }
$zipHash = (Get-FileHash -LiteralPath $PackageZip -Algorithm SHA256).Hash
if ($zipHash -ne $expected[$zipName]) {
    Stop-WithError "Package SHA256 mismatch for $zipName. Expected $($expected[$zipName]), got $zipHash. Do not install this file."
}

Write-Host "Package: $zipName"
Write-Host "Target : $TargetRoot"

$running = Get-Process -Name $AppName -ErrorAction SilentlyContinue
if ($running -and -not $NoStopProcess) {
    Write-Host "Stopping running $AppName process(es)..."
    foreach ($p in $running) {
        try { $p.CloseMainWindow() | Out-Null } catch { }
    }
    Start-Sleep -Seconds 4
    Get-Process -Name $AppName -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
    if (Get-Process -Name $AppName -ErrorAction SilentlyContinue) {
        Stop-WithError "Cannot stop $AppName. Close it manually and run this script again."
    }
}

if (Test-Path -LiteralPath $TargetRoot) {
    $backup = "$TargetRoot.backup-" + (Get-Date -Format "yyyyMMdd-HHmmss")
    Write-Host "Backing up current install to: $backup"
    Copy-Item -LiteralPath $TargetRoot -Destination $backup -Recurse -Force
}

$tempRoot = Join-Path $env:TEMP ("doubao-install-" + [guid]::NewGuid().ToString("N"))
try {
    New-Item -ItemType Directory -Path $tempRoot -Force | Out-Null

    Write-Host "Extracting package..."
    Expand-Archive -LiteralPath $PackageZip -DestinationPath $tempRoot -Force

    $source = Join-Path $tempRoot $AppName
    if (-not (Test-Path -LiteralPath (Join-Path $source "$AppName.exe"))) {
        if (Test-Path -LiteralPath (Join-Path $tempRoot "$AppName.exe")) {
            $source = $tempRoot
        } else {
            Stop-WithError "Package does not contain $AppName.exe."
        }
    }

    $sourceExe = Join-Path $source "$AppName.exe"
    $exeHash = (Get-FileHash -LiteralPath $sourceExe -Algorithm SHA256).Hash
    if ($expected.ContainsKey("$AppName.exe") -and $exeHash -ne $expected["$AppName.exe"]) {
        Stop-WithError "EXE SHA256 mismatch. Expected $($expected["$AppName.exe"]), got $exeHash."
    }

    New-Item -ItemType Directory -Path $TargetRoot -Force | Out-Null
    Write-Host "Copying files..."
    & robocopy $source $TargetRoot /E /R:1 /W:1 /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { Stop-WithError "robocopy failed with exit code $LASTEXITCODE." }

    $targetExe = Join-Path $TargetRoot "$AppName.exe"
    $targetHash = (Get-FileHash -LiteralPath $targetExe -Algorithm SHA256).Hash
    if ($targetHash -ne $exeHash) { Stop-WithError "Installed EXE SHA256 mismatch. Expected $exeHash, got $targetHash." }

    Write-Host ""
    Write-Host "Install OK." -ForegroundColor Green
    Write-Host "EXE SHA256: $targetHash"

    if (-not $NoStart) {
        Write-Host "Starting $AppName..."
        Start-Process -FilePath $targetExe -WorkingDirectory $TargetRoot
        Start-Sleep -Seconds 5
        $started = Get-Process -Name $AppName -ErrorAction SilentlyContinue
        if ($started) {
            Write-Host "Running PID: $($started.Id -join ', ')" -ForegroundColor Green
        } else {
            Write-Host "WARNING: process not detected after 5 seconds. Start it manually from: $targetExe" -ForegroundColor Yellow
        }
    }
}
finally {
    Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
}

Pause-IfNeeded

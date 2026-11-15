param(
    [Parameter(Mandatory = $true)][string]$AppZip,
    [Parameter(Mandatory = $true)][string]$Version,
    [string]$OutputDir = "dist",
    [string]$ReleaseCommit = "",
    [string]$BinaryCommit = ""
)

$ErrorActionPreference = "Stop"
$AppName = "DoubaoMouseBridge"
$Root = Split-Path -Parent $PSScriptRoot
$Output = if ([IO.Path]::IsPathRooted($OutputDir)) { $OutputDir } else { Join-Path $Root $OutputDir }
$Packaging = Join-Path $Root "packaging\installer"

function New-ZipFromDirectory {
    param([string]$Source, [string]$Destination)
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    if (Test-Path -LiteralPath $Destination) { Remove-Item -LiteralPath $Destination -Force }
    $archive = [IO.Compression.ZipFile]::Open($Destination, [IO.Compression.ZipArchiveMode]::Create)
    try {
        foreach ($file in Get-ChildItem -LiteralPath $Source -Recurse -File) {
            $relative = $file.FullName.Substring($Source.Length).TrimStart('\', '/').Replace('\', '/')
            [IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
                $archive, $file.FullName, $relative, [IO.Compression.CompressionLevel]::Optimal) | Out-Null
        }
    }
    finally {
        $archive.Dispose()
    }
}

if (-not (Test-Path -LiteralPath $AppZip)) { throw "App package not found: $AppZip" }
New-Item -ItemType Directory -Path $Output -Force | Out-Null

$appPackageName = "DoubaoMouseBridge-v$Version-app.zip"
$installerName = "DoubaoMouseBridge-v$Version-installer.zip"
$appOut = Join-Path $Output $appPackageName
$installerOut = Join-Path $Output $installerName
foreach ($path in @($appOut, $installerOut, "$installerOut.sha256")) {
    if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Force }
}

$work = Join-Path ([IO.Path]::GetTempPath()) ("doubao-installer-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $work -Force | Out-Null
try {
    $unpack = Join-Path $work "app"
    Expand-Archive -LiteralPath $AppZip -DestinationPath $unpack -Force

    $appDir = Join-Path $unpack $AppName
    if (-not (Test-Path -LiteralPath $appDir)) { $appDir = $unpack }
    $exe = Join-Path $appDir "$AppName.exe"
    if (-not (Test-Path -LiteralPath $exe)) { throw "App package does not contain $AppName.exe" }
    $exeSha = (Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash.ToLowerInvariant()

    $buildInfo = Join-Path $appDir "BUILD-INFO.json"
    if (Test-Path -LiteralPath $buildInfo) {
        $info = Get-Content -LiteralPath $buildInfo -Raw | ConvertFrom-Json
        $info | Add-Member -NotePropertyName release_version -NotePropertyValue $Version -Force
        $info | Add-Member -NotePropertyName release_commit -NotePropertyValue $ReleaseCommit -Force
        $info | Add-Member -NotePropertyName binary_built_from_commit -NotePropertyValue $BinaryCommit -Force
        $info | Add-Member -NotePropertyName exe_sha256 -NotePropertyValue $exeSha -Force
        $info | Add-Member -NotePropertyName packaged_at -NotePropertyValue (Get-Date).ToUniversalTime().ToString("yyyy-MM-dd HH:mm:ss 'UTC'") -Force
        $info | ConvertTo-Json | Set-Content -LiteralPath $buildInfo -Encoding UTF8
    }

    New-ZipFromDirectory -Source $appDir -Destination $appOut
    $appSha = (Get-FileHash -LiteralPath $appOut -Algorithm SHA256).Hash.ToLowerInvariant()

    $stage = Join-Path $work "installer"
    New-Item -ItemType Directory -Path $stage -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $Packaging "install.cmd") -Destination $stage
    Copy-Item -LiteralPath (Join-Path $Packaging "install.ps1") -Destination $stage
    Copy-Item -LiteralPath (Join-Path $Packaging "README.txt") -Destination $stage
    Copy-Item -LiteralPath $appOut -Destination $stage
    $checksums = "$appSha  $appPackageName`n$exeSha  $AppName.exe`n"
    Set-Content -LiteralPath (Join-Path $stage "checksums.txt") -Value $checksums -Encoding ASCII -NoNewline

    New-ZipFromDirectory -Source $stage -Destination $installerOut
    $installerSha = (Get-FileHash -LiteralPath $installerOut -Algorithm SHA256).Hash.ToLowerInvariant()
    Set-Content -LiteralPath "$installerOut.sha256" -Value "$installerSha  $installerName`n" -Encoding ASCII -NoNewline

    Write-Host "APP      : $appOut"
    Write-Host "APP SHA  : $appSha"
    Write-Host "EXE SHA  : $exeSha"
    Write-Host "INSTALLER: $installerOut"
    Write-Host "SHA256   : $installerSha"
}
finally {
    Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
}

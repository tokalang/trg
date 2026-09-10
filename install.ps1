# trg standalone installer for Windows PowerShell
#
# Default installation:
#   irm https://raw.githubusercontent.com/tokalang/trg/main/install.ps1 | iex
#
# Custom options:
#   & ([scriptblock]::Create((irm https://raw.githubusercontent.com/tokalang/trg/main/install.ps1))) -InstallDir "$HOME\bin" -NoModifyPath

[CmdletBinding()]
param (
    [string]$Version = "v0.17.0",
    [string]$InstallDir = "$env:LOCALAPPDATA\Programs\trg",
    [switch]$NoModifyPath,
    [switch]$Help
)

$ErrorActionPreference = "Stop"

if ($Help) {
    Write-Host @"
trg Windows Installer

Usage:
    install.ps1 [OPTIONS]

Options:
    -Version <str>      Release version tag to install (default: v0.17.0)
    -InstallDir <str>   Directory to install trg.exe into (default: %LOCALAPPDATA%\Programs\trg)
    -NoModifyPath       Do not append InstallDir to user PATH environment variable
    -Help               Show this help message
"@
    exit 0
}

# 1. Validate version format
if ($Version -notmatch '^v\d+\.\d+\.\d+') {
    Write-Error "Invalid version '$Version'. Expected format like 'v0.17.0'."
    exit 1
}

# 2. Detect processor architecture
$arch = switch ($env:PROCESSOR_ARCHITECTURE) {
    "ARM64" { "arm64" }
    "AMD64" { "x64" }
    default {
        Write-Warning "Unrecognized PROCESSOR_ARCHITECTURE '$env:PROCESSOR_ARCHITECTURE', falling back to x64."
        "x64"
    }
}

$repo = "tokalang/trg"
$assetName = "trg-$Version-windows-$arch.zip"
$assetUrl = "https://github.com/$repo/releases/download/$Version/$assetName"
$shaUrl = "https://github.com/$repo/releases/download/$Version/SHA256SUMS"

Write-Host "Installing trg $Version ($arch) for Windows..." -ForegroundColor Cyan

# 3. Create a temporary scratch directory
$tempDir = Join-Path ([System.IO.Path]::GetTempPath()) ("trg-install-" + [System.Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $tempDir -Force | Out-Null

try {
    # Configure TLS 1.2+
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 -bor [Net.SecurityProtocolType]::Tls13

    # 4. Download SHA256SUMS
    Write-Host "  → Downloading checksums from $shaUrl..."
    $sumsFile = Join-Path $tempDir "SHA256SUMS"
    Invoke-WebRequest -Uri $shaUrl -OutFile $sumsFile -UseBasicParsing

    $expectedSha = $null
    Get-Content $sumsFile | ForEach-Object {
        $parts = $_ -split '\s+'
        if ($parts.Count -ge 2 -and $parts[1] -eq $assetName) {
            $expectedSha = $parts[0].Trim().ToUpper()
        }
    }

    if (-not $expectedSha) {
        throw "Could not locate checksum for $assetName in SHA256SUMS."
    }

    # 5. Download Release ZIP
    Write-Host "  → Downloading $assetName..."
    $zipFile = Join-Path $tempDir $assetName
    Invoke-WebRequest -Uri $assetUrl -OutFile $zipFile -UseBasicParsing

    # 6. Verify SHA-256
    Write-Host "  → Verifying SHA-256 checksum..."
    $actualSha = (Get-FileHash -Path $zipFile -Algorithm SHA256).Hash.ToUpper()
    if ($actualSha -ne $expectedSha) {
        throw "SHA-256 verification failed!`nExpected: $expectedSha`nActual:   $actualSha"
    }
    Write-Host "  ✓ Checksum verified ($actualSha)" -ForegroundColor Green

    # 7. Extract archive
    Write-Host "  → Extracting archive..."
    $extractDir = Join-Path $tempDir "extracted"
    Expand-Archive -Path $zipFile -DestinationPath $extractDir -Force

    $binarySource = Get-ChildItem -Path $extractDir -Recurse -Filter "trg.exe" | Select-Object -First 1
    if (-not $binarySource) {
        throw "Could not find trg.exe inside extracted archive."
    }

    # 8. Install to destination
    if (-not (Test-Path $InstallDir)) {
        New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
    }

    $targetExe = Join-Path $InstallDir "trg.exe"
    Copy-Item -Path $binarySource.FullName -Destination $targetExe -Force
    Write-Host "  ✓ Binary installed to $targetExe" -ForegroundColor Green

    # 9. Configure PATH
    if (-not $NoModifyPath) {
        $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
        $dirs = if ($userPath) { $userPath -split ';' } else { @() }
        if ($dirs -notcontains $InstallDir) {
            Write-Host "  → Adding $InstallDir to User PATH..."
            $newPath = if ($userPath) { "$userPath;$InstallDir" } else { $InstallDir }
            [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
            if (($env:Path -split ';') -notcontains $InstallDir) {
                $env:Path = "$env:Path;$InstallDir"
            }
            Write-Host "  ✓ User PATH updated" -ForegroundColor Green
        } else {
            Write-Host "  ✓ $InstallDir is already in User PATH" -ForegroundColor Green
        }
    }

    Write-Host ""
    Write-Host "🎉 trg $Version has been successfully installed!" -ForegroundColor Green
    Write-Host "To verify installation, open a new terminal and run:" -ForegroundColor White
    Write-Host "    trg --version" -ForegroundColor Yellow

} finally {
    if (Test-Path $tempDir) {
        Remove-Item -Path $tempDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}

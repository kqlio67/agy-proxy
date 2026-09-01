# ==============================================================================
# Antigravity Proxy 1-Click Installer (Windows PowerShell)
# Usage: irm https://raw.githubusercontent.com/kqlio67/agy-proxy/main/install.ps1 | iex
# ==============================================================================

$ErrorActionPreference = "Stop"

$Repo = "kqlio67/agy-proxy"
$InstallDir = "$env:LOCALAPPDATA\Programs\agy-proxy"
$TargetAsset = "agy-proxy-windows-amd64.zip"

Write-Host "`nAntigravity Proxy Quick Installer (Windows)`n" -ForegroundColor Cyan

# 1. Fetch latest release from GitHub API
Write-Host "Finding latest release from GitHub..." -ForegroundColor Gray

try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $ReleaseInfo = Invoke-RestMethod -Uri "https://api.github.com/repos/$Repo/releases/latest" -Headers @{"User-Agent"="agy-proxy-installer"}
    $LatestTag = $ReleaseInfo.tag_name
} catch {
    Write-Host "Failed to fetch latest release from GitHub API: $_" -ForegroundColor Red
    exit 1
}

if (-not $LatestTag) {
    Write-Host "Could not determine latest release tag." -ForegroundColor Red
    exit 1
}

$DownloadUrl = "https://github.com/$Repo/releases/download/$LatestTag/$TargetAsset"
Write-Host "Downloading $LatestTag from $DownloadUrl..." -ForegroundColor Green

# 2. Download and Extract
$TempZip = Join-Path $env:TEMP "agy-proxy-win-download.zip"
$TempExtract = Join-Path $env:TEMP "agy-proxy-extracted"

if (Test-Path $TempZip) { Remove-Item -Force $TempZip }
if (Test-Path $TempExtract) { Remove-Item -Recurse -Force $TempExtract }

Invoke-WebRequest -Uri $DownloadUrl -OutFile $TempZip -UseBasicParsing
Expand-Archive -Path $TempZip -DestinationPath $TempExtract -Force

# 3. Install to Program Directory
if (-not (Test-Path $InstallDir)) {
    New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
}

$ExeSource = Get-ChildItem -Path $TempExtract -Filter "agy-proxy.exe" -Recurse | Select-Object -First 1
if (-not $ExeSource) {
    Write-Host "Could not find agy-proxy.exe inside downloaded archive." -ForegroundColor Red
    exit 1
}

$TargetExe = Join-Path $InstallDir "agy-proxy.exe"
Copy-Item -Path $ExeSource.FullName -Destination $TargetExe -Force

# Cleanup temp files
Remove-Item -Force $TempZip -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force $TempExtract -ErrorAction SilentlyContinue

Write-Host "Installed binary to: $TargetExe" -ForegroundColor Green

# 4. Add to User PATH if not present
$UserPath = [Environment]::GetEnvironmentVariable("Path", [EnvironmentVariableTarget]::User)
if ($UserPath -notlike "*$InstallDir*") {
    Write-Host "Adding $InstallDir to User PATH..." -ForegroundColor Yellow
    [Environment]::SetEnvironmentVariable("Path", "$UserPath;$InstallDir", [EnvironmentVariableTarget]::User)
    $env:Path = "$env:Path;$InstallDir"
}

Write-Host "`nAntigravity Proxy successfully installed!" -ForegroundColor Green
Write-Host "To start the proxy, open a new terminal and run:`n"
Write-Host "  agy-proxy`n" -ForegroundColor Cyan

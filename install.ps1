# ==============================================================================
# ⚡ Antigravity Proxy 1-Click Installer (Windows PowerShell)
# Usage: irm https://raw.githubusercontent.com/kqlio67/agy-proxy/main/install.ps1 | iex
# ==============================================================================

$ErrorActionPreference = "Stop"

# Ensure UTF-8 console output for symbols (■, ·)
try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $OutputEncoding = [System.Text.Encoding]::UTF8
} catch {}

$Repo = "kqlio67/agy-proxy"
$App = "agy-proxy"
$InstallDir = "$env:LOCALAPPDATA\Programs\agy-proxy"
$TargetAsset = "agy-proxy-windows-amd64.zip"

$esc = [char]27
$Orange = "$esc[38;5;214m"
$Reset = "$esc[0m"
$Muted = "$esc[0;2m"
$Green = "$esc[0;32m"
$Cyan = "$esc[0;36m"
$Yellow = "$esc[1;33m"
$Red = "$esc[0;31m"

# 1. Fetch latest release tag (API with HEAD redirect fallback)
$LatestTag = $null
try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $ReleaseInfo = Invoke-RestMethod -Uri "https://api.github.com/repos/$Repo/releases/latest" -Headers @{"User-Agent"="agy-proxy-installer"}
    $LatestTag = $ReleaseInfo.tag_name
} catch {
    try {
        $req = [System.Net.HttpWebRequest]::Create("https://github.com/$Repo/releases/latest")
        $req.Method = "HEAD"
        $req.AllowAutoRedirect = $true
        $req.UserAgent = "agy-proxy-installer"
        $resp = $req.GetResponse()
        $LatestTag = $resp.ResponseUri.Segments[-1].TrimEnd('/')
        $resp.Dispose()
    } catch {}
}

if (-not $LatestTag) {
    Write-Host "❌ Could not determine latest release tag. Please check your internet connection." -ForegroundColor Red
    exit 1
}

$DisplayVersion = $LatestTag.TrimStart('v')
$DownloadUrl = "https://github.com/$Repo/releases/download/$LatestTag/$TargetAsset"

# 2. Download with progress bar function
function Download-WithProgress {
    param (
        [string]$Url,
        [string]$Destination
    )

    $request = [System.Net.HttpWebRequest]::Create($Url)
    $request.Method = "GET"
    $request.UserAgent = "agy-proxy-installer"
    $request.AllowAutoRedirect = $true
    $request.Timeout = 60000

    $response = $request.GetResponse()
    $totalBytes = $response.ContentLength
    $stream = $response.GetResponseStream()
    $fileStream = [System.IO.File]::Create($Destination)
    $buffer = New-Object byte[] 65536
    $totalRead = 0
    $width = 50

    try { [Console]::CursorVisible = $false } catch {}

    try {
        $lastUpdate = [DateTime]::MinValue
        while (($read = $stream.Read($buffer, 0, $buffer.Length)) -gt 0) {
            $fileStream.Write($buffer, 0, $read)
            $totalRead += $read

            $now = [DateTime]::UtcNow
            if (($now - $lastUpdate).TotalMilliseconds -ge 80 -or $totalRead -eq $totalBytes) {
                $lastUpdate = $now
                if ($totalBytes -gt 0) {
                    $pct = [int](($totalRead * 100) / $totalBytes)
                    if ($pct -gt 100) { $pct = 100 }
                    $on = [int](($pct * $width) / 100)
                    $off = $width - $on
                    $filled = [string]::new([char]0x25A0, $on)
                    $empty = [string]::new([char]0x00B7, $off)
                    $percentStr = "{0,3}%" -f $pct

                    Write-Host "`r$Orange$filled$empty $percentStr$Reset" -NoNewline
                }
            }
        }
        Write-Host ""
    } finally {
        try { [Console]::CursorVisible = $true } catch {}
        if ($fileStream) { $fileStream.Dispose() }
        if ($stream) { $stream.Dispose() }
        if ($response) { $response.Dispose() }
    }
}

# 3. Banner & Download
Write-Host "`nInstalling $App version: $DisplayVersion"

$TempZip = Join-Path $env:TEMP "agy-proxy-win-download.zip"
$TempExtract = Join-Path $env:TEMP "agy-proxy-extracted"

if (Test-Path $TempZip) { Remove-Item -Force $TempZip }
if (Test-Path $TempExtract) { Remove-Item -Recurse -Force $TempExtract }

try {
    Download-WithProgress -Url $DownloadUrl -Destination $TempZip
} catch {
    Write-Host "`nRetrying with standard download..." -ForegroundColor Yellow
    Invoke-WebRequest -Uri $DownloadUrl -OutFile $TempZip -UseBasicParsing
}

# 4. Extract
Expand-Archive -Path $TempZip -DestinationPath $TempExtract -Force

# 5. Install to Program Directory
if (-not (Test-Path $InstallDir)) {
    New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
}

$ExeSource = Get-ChildItem -Path $TempExtract -Filter "agy-proxy.exe" -Recurse | Select-Object -First 1
if (-not $ExeSource) {
    Write-Host "❌ Could not find agy-proxy.exe inside downloaded archive." -ForegroundColor Red
    exit 1
}

$TargetExe = Join-Path $InstallDir "agy-proxy.exe"
Copy-Item -Path $ExeSource.FullName -Destination $TargetExe -Force

# Cleanup temp files
Remove-Item -Force $TempZip -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force $TempExtract -ErrorAction SilentlyContinue

Write-Host "✓ Successfully installed $App to $TargetExe" -ForegroundColor Green

# 6. Add to User PATH if not present
$UserPath = [Environment]::GetEnvironmentVariable("Path", [EnvironmentVariableTarget]::User)
if ($UserPath -notlike "*$InstallDir*") {
    Write-Host "`nAdding $InstallDir to User PATH..." -ForegroundColor Yellow
    [Environment]::SetEnvironmentVariable("Path", "$UserPath;$InstallDir", [EnvironmentVariableTarget]::User)
    $env:Path = "$env:Path;$InstallDir"
}

Write-Host "`n⚡ Antigravity Proxy is ready!" -ForegroundColor Green
Write-Host "To start the proxy gateway, open a new terminal and run:`n"
Write-Host "  agy-proxy`n" -ForegroundColor Cyan

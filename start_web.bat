@echo off
setlocal enabledelayedexpansion

REM ===========================================================================
REM Antigravity Web Launcher for Windows (Zero-Electron Overhead)
REM Starts Antigravity in browser-based Web mode, saving RAM.
REM ===========================================================================

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

where uv >nul 2>&1
if %ERRORLEVEL% equ 0 (
    uv run python -m agy_proxy.cli web %*
    goto :eof
)

where python >nul 2>&1
if %ERRORLEVEL% equ 0 (
    python -m agy_proxy.cli web %*
    goto :eof
)

echo [agy-proxy] Python 3.10+ or uv is required to run Antigravity Web Mode.
pause

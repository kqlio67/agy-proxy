@echo off
rem ==============================================================================
rem Antigravity Proxy 1-Click Installer (Windows Command Prompt)
rem Usage: curl -fsSL https://raw.githubusercontent.com/kqlio67/agy-proxy/main/install.cmd -o install.cmd && install.cmd && del install.cmd
rem ==============================================================================

echo.
echo ===================================================
echo   Antigravity Proxy Windows Installer
echo ===================================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/kqlio67/agy-proxy/main/install.ps1 | iex"

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERROR] Installation failed.
    exit /b %ERRORLEVEL%
)

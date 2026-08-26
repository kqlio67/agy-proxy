@echo off
setlocal
title Antigravity Proxy Server

cd /d "%~dp0"

where uv >nul 2>nul
if %ERRORLEVEL% equ 0 (
    echo [Antigravity Proxy] Starting via UV...
    uv run python main.py %*
    goto :eof
)

where python >nul 2>nul
if %ERRORLEVEL% equ 0 (
    echo [Antigravity Proxy] Starting via Python...
    python main.py %*
    goto :eof
)

echo [ERROR] Neither 'uv' nor 'python' was found in your PATH.
echo Please install Python 3.10+ from https://www.python.org or UV from https://astral.sh/uv
pause

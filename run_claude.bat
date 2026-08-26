@echo off
setlocal
title Claude Code with Antigravity Proxy

set "PORT=8000"
set "MODEL=gemini-3.7-flash-high"

:parse_args
if "%~1"=="" goto :done_args
if /i "%~1"=="-p" set "PORT=%~2" & shift & shift & goto :parse_args
if /i "%~1"=="--port" set "PORT=%~2" & shift & shift & goto :parse_args
if /i "%~1"=="-m" set "MODEL=%~2" & shift & shift & goto :parse_args
if /i "%~1"=="--model" set "MODEL=%~2" & shift & shift & goto :parse_args
shift
goto :parse_args

:done_args
set "ANTHROPIC_BASE_URL=http://127.0.0.1:%PORT%"
set "ANTHROPIC_API_KEY=dummy"
set "ANTHROPIC_MODEL=%MODEL%"

echo ===================================================
echo   Launching Claude Code with Antigravity Proxy
echo   * URL:   %ANTHROPIC_BASE_URL%
echo   * Model: %ANTHROPIC_MODEL%
echo ===================================================

where claude >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo [ERROR] 'claude' CLI was not found. Install it via: npm install -g @anthropic-ai/claude-code
    pause
    exit /b 1
)

claude %*

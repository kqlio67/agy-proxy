@echo off
setlocal enabledelayedexpansion
title Claude Code with Antigravity Proxy

set "PORT=8000"
set "MODEL=anthropic.gemini-3.7-flash-high"
set "CUSTOM_URL="
set "CLAUDE_ARGS="

:parse_args
if "%~1"=="" goto :done_args
if /i "%~1"=="--port" (
    set "PORT=%~2"
    shift & shift
    goto :parse_args
)
if /i "%~1"=="--url" (
    set "CUSTOM_URL=%~2"
    shift & shift
    goto :parse_args
)
if /i "%~1"=="-m" (
    set "MODEL=%~2"
    set "CLAUDE_ARGS=!CLAUDE_ARGS! --model %~2"
    shift & shift
    goto :parse_args
)
if /i "%~1"=="--model" (
    set "MODEL=%~2"
    set "CLAUDE_ARGS=!CLAUDE_ARGS! --model %~2"
    shift & shift
    goto :parse_args
)
if /i "%~1"=="--proxy-help" (
    echo Usage: run_claude.bat [--port PORT] [--url URL] [-m/--model MODEL] [CLAUDE_ARGS...]
    echo All other Claude arguments (e.g. -c, -r, -p, doctor) are passed directly to Claude Code.
    exit /b 0
)
if "%~1"=="--" (
    shift
    :collect_rest
    if "%~1"=="" goto :done_args
    set "CLAUDE_ARGS=!CLAUDE_ARGS! %1"
    shift
    goto :collect_rest
)
set "CLAUDE_ARGS=!CLAUDE_ARGS! %1"
shift
goto :parse_args

:done_args
if defined CUSTOM_URL (
    set "ANTHROPIC_BASE_URL=%CUSTOM_URL%"
) else (
    set "ANTHROPIC_BASE_URL=http://127.0.0.1:%PORT%"
)
set "ANTHROPIC_API_KEY=dummy"
set "ANTHROPIC_MODEL=%MODEL%"
set "ANTHROPIC_SMALL_FAST_MODEL=%MODEL%"

rem Check if user passed permission skip args
echo %CLAUDE_ARGS% | findstr /i /c:"--dangerously-skip-permissions" /c:"--permission-mode" >nul
if %ERRORLEVEL% neq 0 (
    echo.
    set /p "PERM_CHOICE=Enable auto-approval of actions without prompts? (--dangerously-skip-permissions) [y/N]: "
    if /i "!PERM_CHOICE!"=="y" (
        set "CLAUDE_ARGS=!CLAUDE_ARGS! --dangerously-skip-permissions"
    )
)

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

claude --settings "%AGY_SETTINGS%" %CLAUDE_ARGS%

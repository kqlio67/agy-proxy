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

rem Override model picker entries so /model shows proxy models
if not defined ANTHROPIC_DEFAULT_HAIKU_MODEL set "ANTHROPIC_DEFAULT_HAIKU_MODEL=anthropic.gemini-3.1-flash-lite"
if not defined ANTHROPIC_DEFAULT_HAIKU_MODEL_NAME set "ANTHROPIC_DEFAULT_HAIKU_MODEL_NAME=Gemini 3.1 Flash Lite"
if not defined ANTHROPIC_DEFAULT_HAIKU_MODEL_DESCRIPTION set "ANTHROPIC_DEFAULT_HAIKU_MODEL_DESCRIPTION=Fast & lightweight via Antigravity Proxy"

if not defined ANTHROPIC_DEFAULT_SONNET_MODEL set "ANTHROPIC_DEFAULT_SONNET_MODEL=anthropic.gemini-3.7-flash-high"
if not defined ANTHROPIC_DEFAULT_SONNET_MODEL_NAME set "ANTHROPIC_DEFAULT_SONNET_MODEL_NAME=Gemini 3.7 Flash High"
if not defined ANTHROPIC_DEFAULT_SONNET_MODEL_DESCRIPTION set "ANTHROPIC_DEFAULT_SONNET_MODEL_DESCRIPTION=High quality coding via Antigravity Proxy"

if not defined ANTHROPIC_DEFAULT_OPUS_MODEL set "ANTHROPIC_DEFAULT_OPUS_MODEL=anthropic.claude-opus-4-6-thinking"
if not defined ANTHROPIC_DEFAULT_OPUS_MODEL_NAME set "ANTHROPIC_DEFAULT_OPUS_MODEL_NAME=Claude Opus 4.6 Thinking"
if not defined ANTHROPIC_DEFAULT_OPUS_MODEL_DESCRIPTION set "ANTHROPIC_DEFAULT_OPUS_MODEL_DESCRIPTION=Most capable via Antigravity Proxy"

if not defined ANTHROPIC_DEFAULT_FABLE_MODEL set "ANTHROPIC_DEFAULT_FABLE_MODEL=anthropic.gemini-pro-agent"
if not defined ANTHROPIC_DEFAULT_FABLE_MODEL_NAME set "ANTHROPIC_DEFAULT_FABLE_MODEL_NAME=Gemini Pro Agent (3.1 Pro)"
if not defined ANTHROPIC_DEFAULT_FABLE_MODEL_DESCRIPTION set "ANTHROPIC_DEFAULT_FABLE_MODEL_DESCRIPTION=Best for long-running tasks via Antigravity Proxy"

set "DISABLE_TELEMETRY=1"
set "DISABLE_ERROR_REPORTING=1"
set "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1"
set "CLAUDE_CODE_DISABLE_UNKNOWN_MODEL_WINDOW_ENFORCEMENT=1"
set "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS=1"
if not defined CLAUDE_CODE_MAX_CONTEXT_TOKENS set "CLAUDE_CODE_MAX_CONTEXT_TOKENS=1048576"

rem Build availableModels settings JSON (anthropic. prefix required for Claude Code to register them)
set "AGY_SETTINGS={"availableModels":["anthropic.gemini-3.7-flash-high","anthropic.gemini-3.7-flash-medium","anthropic.gemini-3.7-flash-low","anthropic.gemini-3.6-flash-high","anthropic.gemini-3.6-flash-medium","anthropic.gemini-3.6-flash-low","anthropic.gemini-3.5-flash-low","anthropic.gemini-pro-agent","anthropic.gemini-3.1-pro-low","anthropic.gemini-3.1-flash-lite","anthropic.gemini-3.1-flash-image","anthropic.gemini-3-flash","anthropic.gemini-2.5-pro","anthropic.gemini-2.5-flash","anthropic.claude-sonnet-4-6","anthropic.claude-opus-4-6-thinking","anthropic.gpt-oss-120b-medium"]}"

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

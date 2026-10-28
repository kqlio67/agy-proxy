@echo off
setlocal enabledelayedexpansion
title OpenAI Codex CLI with Antigravity Proxy

set "PORT=8000"
set "HOST=127.0.0.1"
set "DEFAULT_MODEL=gemini-3.8-flash-high"
if defined CODEX_MODEL set "DEFAULT_MODEL=%CODEX_MODEL%"
set "CUSTOM_URL="
set "CODEX_ARGS="
set "MODEL_SPECIFIED="

:parse_args
if "%~1"=="" goto :done_args
if /i "%~1"=="--port" (
    set "PORT=%~2"
    shift & shift
    goto :parse_args
)
if /i "%~1"=="-p" (
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
    set "DEFAULT_MODEL=%~2"
    set "MODEL_SPECIFIED=1"
    set "CODEX_ARGS=!CODEX_ARGS! -m %~2"
    shift & shift
    goto :parse_args
)
if /i "%~1"=="--model" (
    set "DEFAULT_MODEL=%~2"
    set "MODEL_SPECIFIED=1"
    set "CODEX_ARGS=!CODEX_ARGS! -m %~2"
    shift & shift
    goto :parse_args
)
if /i "%~1"=="--proxy-help" (
    echo Usage: run_codex.bat [--port PORT] [--url URL] [-m/--model MODEL] [CODEX_ARGS...]
    echo Examples:
    echo   run_codex.bat
    echo   run_codex.bat -m claude-sonnet-4-6
    echo   run_codex.bat -m gpt-6-astra
    echo   run_codex.bat exec "Explain main.py"
    echo All native Codex arguments are passed directly to Codex CLI.
    exit /b 0
)
if "%~1"=="--" (
    shift
    :collect_rest
    if "%~1"=="" goto :done_args
    set "CODEX_ARGS=!CODEX_ARGS! %1"
    shift
    goto :collect_rest
)
set "CODEX_ARGS=!CODEX_ARGS! %1"
shift
goto :parse_args

:done_args
set "SCRIPT_DIR=%~dp0"
set "CATALOG_PATH=%SCRIPT_DIR%agy_proxy\codex_models.json"
if not exist "%CATALOG_PATH%" set "CATALOG_PATH=%SCRIPT_DIR%codex_models.json"

if not exist "%CATALOG_PATH%" (
    echo [ERROR] codex_models.json not found in %SCRIPT_DIR%
    pause
    exit /b 1
)

where codex >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Codex CLI ('codex') was not found in your PATH.
    echo Install it first via npm: npm install -g @openai/codex
    pause
    exit /b 1
)

if not defined MODEL_SPECIFIED (
    set "CODEX_ARGS=-m %DEFAULT_MODEL% !CODEX_ARGS!"
)

if defined CUSTOM_URL (
    set "PROXY_URL=%CUSTOM_URL%"
) else (
    set "PROXY_URL=http://%HOST%:%PORT%"
)

set "API_URL=%PROXY_URL%/v1"

set "OPENAI_BASE_URL=%API_URL%"
if not defined OPENAI_API_KEY set "OPENAI_API_KEY=dummy"

:: Disable OpenTelemetry / metrics
set "OTEL_SDK_DISABLED=true"
set "DO_NOT_TRACK=1"
set "CODEX_TELEMETRY=0"
set "OTEL_EXPORTER_OTLP_ENDPOINT=%PROXY_URL%"
set "OTEL_EXPORTER_OTLP_PROTOCOL=http/json"

echo ===================================================
echo   Launching OpenAI Codex CLI with Antigravity Proxy
echo   * Base URL: %API_URL%
echo   * Catalog:  %CATALOG_PATH%
echo   * Model:    %DEFAULT_MODEL%
echo ===================================================

codex --no-daemon -c openai_base_url="%API_URL%" -c model_catalog_json="%CATALOG_PATH%" -c analytics.enabled=false %CODEX_ARGS%

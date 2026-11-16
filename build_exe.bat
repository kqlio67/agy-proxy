@echo off
setlocal enabledelayedexpansion

echo ==================================================
echo   Building Antigravity Proxy Standalone Executable (.exe)
echo ==================================================

python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python is not installed or not in PATH. Please install Python 3.10+ and try again.
    pause
    exit /b 1
)

echo [1/3] Upgrading pip and build tools...
python -m pip install --upgrade pip

echo [2/3] Installing project dependencies and PyInstaller...
pip install -e .
pip install pyinstaller

echo [3/3] Running binary builder...
python build_binary.py

if errorlevel 1 (
    echo.
    echo [ERROR] Build failed. Please check the logs above.
    pause
    exit /b 1
)

echo.
echo ==================================================
echo   [SUCCESS] Build complete! Executable is located in:
echo   dist\agy-proxy.exe
echo ==================================================
echo.
pause

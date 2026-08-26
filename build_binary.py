#!/usr/bin/env python3
"""
Cross-platform Single-File Binary Builder for Antigravity Proxy.
Packages the entire FastAPI server, static assets, and client into a single executable.
"""

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).parent.resolve()
DIST_DIR = ROOT_DIR / "dist"
BUILD_DIR = ROOT_DIR / "build"
STATIC_DIR = ROOT_DIR / "agy_proxy" / "static"


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def build():
    print("==================================================")
    print("  Building Antigravity Proxy Standalone Binary    ")
    print("==================================================")

    system = platform.system()
    arch = platform.machine().lower()
    if arch in ("x86_64", "amd64"):
        arch_name = "amd64"
    elif arch in ("arm64", "aarch64"):
        arch_name = "arm64"
    else:
        arch_name = arch

    binary_name = "agy-proxy"
    if system == "Windows":
        binary_name += ".exe"

    print(f"Target OS:   {system}")
    print(f"Target Arch: {arch_name}")
    print(f"Output File: {binary_name}\n")

    # PyInstaller command arguments
    sep = ";" if system == "Windows" else ":"
    data_arg = f"{STATIC_DIR}{sep}agy_proxy/static"

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--name",
        "agy-proxy",
        "--onefile",
        "--clean",
        "--add-data",
        data_arg,
        "--collect-all",
        "uvicorn",
        "--collect-all",
        "fastapi",
        "--collect-all",
        "sse_starlette",
        "--hidden-import",
        "uvicorn.logging",
        "--hidden-import",
        "uvicorn.loops",
        "--hidden-import",
        "uvicorn.loops.auto",
        "--hidden-import",
        "uvicorn.protocols",
        "--hidden-import",
        "uvicorn.protocols.http",
        "--hidden-import",
        "uvicorn.protocols.http.auto",
        "--hidden-import",
        "uvicorn.lifespan",
        "--hidden-import",
        "uvicorn.lifespan.on",
        str(ROOT_DIR / "main.py"),
    ]

    print("Running PyInstaller...")
    res = subprocess.run(cmd, cwd=ROOT_DIR)
    if res.returncode != 0:
        print(f"\n[ERROR] Build failed with exit code {res.returncode}")
        sys.exit(res.returncode)

    built_file = DIST_DIR / binary_name
    if built_file.exists():
        size_mb = built_file.stat().st_size / (1024 * 1024)
        print("\n==================================================")
        print(f"  [SUCCESS] Standalone binary built: {built_file}")
        print(f"  [PACKAGE] Size: {size_mb:.2f} MB")
        print("==================================================\n")
        print("To run:")
        if system == "Windows":
            print("  dist\\agy-proxy.exe --port 8000")
        else:
            print("  ./dist/agy-proxy --port 8000")
    else:
        print(f"\n[ERROR] Output binary not found at {built_file}")
        sys.exit(1)


if __name__ == "__main__":
    build()

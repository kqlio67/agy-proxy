#!/usr/bin/env python3
"""
Entry point for running Antigravity Proxy.
"""

import sys
from agy_proxy.cli import main

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[Antigravity Proxy] Operation cancelled by user.")
        sys.exit(0)

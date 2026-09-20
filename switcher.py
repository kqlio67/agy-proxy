#!/usr/bin/env python3
"""
Antigravity Session Switcher CLI wrapper.

Enables switching active Google Antigravity CLI and IDE sessions between accounts
stored in accounts.json (e.g. when quota limits are reached on one account).

Usage:
    python switcher.py [account_email_name_id_or_index] [--cli | --ide | --both]
    python switcher.py usage [account]
    python switcher.py --usage (-u)
    python switcher.py --next [--cli | --ide]
    python switcher.py --list
"""

import sys
from pathlib import Path

# Ensure root directory is in sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from agy_proxy.switcher import main

if __name__ == "__main__":
    main()

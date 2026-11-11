"""
Entrypoint for `python -m agy_proxy`.
"""

import sys
from agy_proxy.cli import main

if __name__ == "__main__":
    sys.exit(main())

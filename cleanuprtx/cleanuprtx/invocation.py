"""How to invoke this tool, as the owner actually ran it. No package imports."""

import os
import sys


def prog() -> str:
    argv0 = os.path.basename(sys.argv[0] or "")
    if argv0 in ("__main__.py", "") or argv0.endswith("__main__.py"):
        return "python3 -m cleanuprtx"
    return "cleanuprtx"

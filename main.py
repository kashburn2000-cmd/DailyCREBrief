#!/usr/bin/env python3
"""Convenience entry point so you can run the brief with ``python main.py``.

Identical to ``python -m cre_brief``. Examples:

    python main.py --no-send        # dry run (render + print, never send)
    python main.py                  # build and send to RECIPIENTS
"""

import sys

from cre_brief.__main__ import main

if __name__ == "__main__":
    sys.exit(main())

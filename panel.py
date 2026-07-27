#!/usr/bin/env python3
"""Backward-compatible launcher. The application lives in app.py."""
import sys
from app import *

if __name__ == "__main__":
    if "--setup-account" in sys.argv:
        raise SystemExit(setup_initial_account())
    main()

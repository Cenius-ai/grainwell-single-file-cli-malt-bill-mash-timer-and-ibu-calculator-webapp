#!/usr/bin/env python3
"""Convenience runner: `python3 main.py ...` behaves exactly like
`python3 grainwell.py ...` (the whole tool lives in grainwell.py)."""

import sys

import grainwell

if __name__ == "__main__":
    sys.exit(grainwell.main())

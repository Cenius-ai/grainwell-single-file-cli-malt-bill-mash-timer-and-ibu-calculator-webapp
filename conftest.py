"""Path bootstrap so the suite runs from a clean checkout under both unittest
and pytest without any install step (Grainwell ships no packages)."""

import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

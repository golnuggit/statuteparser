"""Pytest configuration for ensuring the repository root is importable."""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure the project root (which contains the statute_to_json package) is on sys.path.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

"""Centralized path resolution — works in both dev and packaged (PyInstaller) mode.

Dev mode:  ROOT_DIR = project root (where run.py lives)
Frozen:    ROOT_DIR = directory containing the .exe
"""

import sys
from pathlib import Path

if getattr(sys, "frozen", False):
    # Running as PyInstaller exe — sys.executable is the .exe path
    ROOT_DIR = Path(sys.executable).parent
else:
    # Running as script — this file is src/utils/paths.py
    ROOT_DIR = Path(__file__).parent.parent.parent

DATA_DIR = ROOT_DIR / "data"
CONFIG_DIR = ROOT_DIR / "config"
STATIC_DIR = ROOT_DIR / "static"

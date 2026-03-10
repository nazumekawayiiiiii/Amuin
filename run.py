#!/usr/bin/env python
"""Amuin launcher. Run from project root: python run.py"""

import sys
from src.main import main

sys.exit(main() or 0)

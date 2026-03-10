"""Claw entry point for python -m src execution."""

import sys
from .main import main

sys.exit(main() or 0)

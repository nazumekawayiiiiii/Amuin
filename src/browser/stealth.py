"""Stealth patches — no-op when using Patchright.

Patchright handles all anti-detection at the binary level:
  - Removes Runtime.enable CDP leak
  - Removes --enable-automation flag
  - Patches navigator.webdriver
  - Disables console API leak
  - Removes other automation fingerprints

No external stealth plugins needed (playwright-stealth would conflict).
This module is kept for backward compatibility but does nothing.
"""

from patchright.sync_api import Page


def apply_stealth(page: Page) -> None:
    """No-op: Patchright handles stealth internally."""
    pass

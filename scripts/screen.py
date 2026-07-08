#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Compatibility wrapper for screen-repro.

The maintained implementation lives in scripts_v3.0/screen.py. This wrapper
prevents accidental execution of the older scripts/screen.py implementation,
which did not enforce the P3 PDF-mapping authenticity gate.
"""
from __future__ import annotations

import runpy
from pathlib import Path

TARGET = Path(__file__).resolve().parents[1] / "scripts_v3.0" / "screen.py"

if __name__ == "__main__":
    runpy.run_path(str(TARGET), run_name="__main__")

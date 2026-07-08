#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compatibility wrapper for the canonical screen-repro v3.0 p3_lock_qc.py."""
from __future__ import annotations

import runpy
from pathlib import Path

CANONICAL = Path(__file__).resolve().parents[1] / "scripts_v3.0" / "p3_lock_qc.py"
if not CANONICAL.exists():
    raise SystemExit(f"Canonical p3_lock_qc.py not found: {CANONICAL}")

runpy.run_path(str(CANONICAL), run_name="__main__")

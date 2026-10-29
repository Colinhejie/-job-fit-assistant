#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Entry point for the job-fit-assistant local core.

    python scripts/jfa.py scan
    python scripts/jfa.py --home ./job-fit-workspace init
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from jfa.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())

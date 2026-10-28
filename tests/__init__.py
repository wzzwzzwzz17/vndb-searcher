# -*- coding: utf-8 -*-
"""galsave 离线测试。运行：python -m unittest discover -s tests -t . -v"""

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

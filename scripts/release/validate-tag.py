#!/usr/bin/env python3
"""Keep the fork release tag tied to the checked-out ttyd source version."""
from pathlib import Path
import re
import sys

source = Path(__file__).resolve().parents[2]
match = re.search(r"project\(ttyd VERSION (\d+\.\d+\.\d+) LANGUAGES C\)",
                  (source / 'CMakeLists.txt').read_text())
if not match:
    raise SystemExit('Cannot determine ttyd project version')
tag = sys.argv[1]
if not re.fullmatch(re.escape(match[1]) + r'-herdr\.\d+', tag):
    raise SystemExit(f'Expected release tag {match[1]}-herdr.N; got {tag!r}')
print(f'Release tag verified: {tag}')

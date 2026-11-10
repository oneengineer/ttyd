#!/usr/bin/env python3
"""Accept numbered fork releases and the image feature's version tags."""
from pathlib import Path
import re
import sys

source = Path(__file__).resolve().parents[2]
match = re.search(r"project\(ttyd VERSION (\d+\.\d+\.\d+) LANGUAGES C\)",
                  (source / 'CMakeLists.txt').read_text())
if not match:
    raise SystemExit('Cannot determine ttyd project version')
tag = sys.argv[1]
if not re.fullmatch(r'(?:' + re.escape(match[1]) + r'-herdr\.\d+|image-v\d+\.\d+\.\d+)', tag):
    raise SystemExit(f'Expected {match[1]}-herdr.N or image-vX.Y.Z; got {tag!r}')
print(f'Release tag verified: {tag}')

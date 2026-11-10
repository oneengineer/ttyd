#!/usr/bin/env python3
"""Generate release notes for standalone ttyd binaries."""
from pathlib import Path
import sys

tag = sys.argv[1]
root = Path(__file__).resolve().parents[2]
print(f'''ttyd fork release **{tag}**.

Includes the first-message WebSocket token authentication fix for browsers that
omit HTTP Authorization during the WebSocket handshake. HTTP authentication and
token verification remain required before a terminal process starts.

| Download | System |
| --- | --- |
| `ttyd.linux-x86_64` | Ubuntu / Linux, Intel or AMD 64-bit |
| `ttyd.linux-aarch64` | Ubuntu / Linux, ARM64 |
| `ttyd.macos-arm64` | Apple Silicon (M1 and newer), macOS 15 or newer |

Linux binaries use musl and static third-party libraries. The Mac binary links
third-party libraries statically and requires only macOS system libraries.
The web terminal frontend is embedded in each binary.

After downloading the correct file, give it execute permission with `chmod +x`
and run it from your terminal. `SHA256SUMS` contains checksums for every asset;
`THIRD_PARTY_LICENSES.tar.gz` contains dependency notices and versions.

These are standalone ttyd binaries; Herdr, Cloudflare Tunnel and launcher scripts
are not included.

Dependency versions:
''')
for line in (root / 'scripts/release/dependencies.env').read_text().splitlines():
    if line.startswith('export '):
        name, version = line.removeprefix('export ').split('=', 1)
        print(f'- {name.removesuffix("_VERSION")}: {version}')

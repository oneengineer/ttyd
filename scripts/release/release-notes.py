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
| `ttyd.windows-x86_64.exe` | Windows 10 1809 or newer / Windows 11, Intel or AMD 64-bit |

Linux binaries use musl and static third-party libraries. The Mac binary links
third-party libraries statically and requires only macOS system libraries.
The web terminal frontend is embedded in each binary.
The Windows executable statically links third-party libraries and the MSVC
runtime, and is tested with native Windows ConPTY.

After downloading the correct file, give it execute permission with `chmod +x`
and run it from your terminal. `SHA256SUMS` contains checksums for every asset;
`THIRD_PARTY_LICENSES.tar.gz` contains Linux/Mac dependency notices and versions.
Windows notices and exact vcpkg dependency versions are in
`THIRD_PARTY_LICENSES_WINDOWS.zip`. Windows users can run the downloaded `.exe`
directly from PowerShell; `chmod` applies only to Linux and macOS.

These are standalone ttyd binaries; Herdr, Cloudflare Tunnel and launcher scripts
are not included.

Linux and macOS dependency versions (Windows uses the pinned vcpkg manifest):
''')
for line in (root / 'scripts/release/dependencies.env').read_text().splitlines():
    if line.startswith('export '):
        name, version = line.removeprefix('export ').split('=', 1)
        print(f'- {name.removesuffix("_VERSION")}: {version}')

# Herdr web terminal

This checkout includes the WebSocket token-authentication fix and the Herdr /
Cloudflare Quick Tunnel launchers used on Linux, macOS and Windows.

## Included behavior

- The frontend supports OSC 52 through xterm.js's ClipboardAddon. The
  launchers use the matching embedded frontend when ttyd supports image paste,
  and a pinned, checksum-verified frontend for older ttyd binaries. They set
  `SSH_TTY` for Herdr's terminal clipboard path and clear inherited Herdr session
  variables before attaching a browser client.
- The launchers select `-s 15` so browser disconnects terminate the attached
  client even when `nohup` has made it inherit ignored SIGHUP. Closing a web
  client or stopping a tunnel preserves the separate Herdr server and its work.
- With `-c`, a missing WebSocket Authorization header can proceed to the existing
  first-message `AuthToken` check. HTTP page/token authentication and rejection
  of incorrect Authorization headers remain in effect. No command starts or
  terminal output is sent before authentication; unauthenticated sockets expire
  after 10 seconds. Malformed initial messages are rejected and failed token
  values are omitted from logs.
- Both launchers offer `list` / `llist` (including `--json`), named or default
  sessions, `start`, `status`, `stop`, `restart` and `logs`. They prevent duplicate
  instances per state directory and print the current URL and browser credentials.
  They do not automatically restart failed tunnels.

Image paste/upload is available with the patched binary. A dedicated
Shift+Enter mapping and additional mobile auto-reconnect behavior are not included.

## Paste images

The default terminal font is 13 px on desktop and 11 px on phones/tablets
(iPhone, iPad, iPod and Android, including iPadOS desktop browsing mode).
Explicit `fontSize` URL parameters or server preferences override this default.

The launchers detect `--image-upload` and enable it automatically on compatible
ttyd builds. They omit `-I` in this case so an old custom HTML file cannot replace
the new embedded frontend. With a stock/older binary, launchers keep the existing
text-only behavior.

Paste a copied screenshot/image into the terminal with the browser's usual paste
action. macOS also supports Ctrl+V through the browser clipboard API. Use the
`＋ Image` button to select a file when the clipboard/browser exposes no image.
Plain text pastes keep xterm.js's existing behavior.

Image bytes are streamed in 64 KiB chunks over the same WebSocket as the terminal,
after login. The server creates a private generated file, acknowledges completion,
and the browser pastes its literal absolute path into the active terminal using
xterm.js's application-controlled bracketed-paste mode. Do not add shell quotes:
Claude's image-path paste handler needs the raw path, even when it contains spaces
or apostrophes. With Claude Code this produces an `[Image #N]` attachment before
submission; the browser does not type the marker or press Enter. Other terminal
applications decide how to handle the pasted path. The agent must run on the
same filesystem as ttyd, or have access to the upload directory; a nested SSH
host/container does not automatically get
these files. This is a file/path bridge, not X11 or remote OS clipboard mirroring.

Codex CLI 0.166.0 was also verified on macOS through ttyd and an existing Herdr
session: pasting with the default temporary directory produced `[Image #1]`, and
the submitted message contained an actual `input_image`. Its path parser differs
from Claude's: the tested custom POSIX directory containing spaces and an
apostrophe was pasted as text instead. Keep the default upload directory when
using Codex; arbitrary custom paths are not yet verified across both agents.

Supported formats are PNG, JPEG, WebP and GIF, with a 20 MiB limit per image.
Uploads are opt-in for standalone ttyd:

```sh
ttyd -W --image-upload herdr
```

`--image-upload-dir /existing/directory` selects an existing absolute/resolvable
base directory. The default is the system temporary directory. Each file gets
its own `ttyd-image-XXXXXX` directory (0700 on Unix) and an exclusive file (0600).
Windows uses libuv UTF-8 filesystem APIs and inherits the base directory's ACL;
choose a directory private to your Windows user. Failed, cancelled, disconnected
and stalled (30 seconds without a chunk) uploads are removed. Successful files
survive browser disconnects and ttyd restarts. Generated directories older than
24 hours are pruned when a later upload begins (at most once per minute); OS temp
cleanup can remove them sooner. They are temporary attachments, not permanent storage.

Only real paste events are needed for normal browser paste. The additional
macOS Ctrl+V clipboard-read route requires HTTPS (or localhost) and browser
permission/user interaction; Cloudflare's HTTPS address satisfies the secure
context requirement. Copying a URL/HTML or copying a file in a file manager may
expose no image bytes; the file button is the fallback. iPhone/Android real-device
clipboard behavior and native Windows runtime still require real-machine checks.

## Build the patched ttyd

Linux/macOS require a C compiler, CMake, libuv, json-c, zlib, a libwebsockets build
with libuv support, and its TLS dependencies. From the repository root, run:

```sh
bash scripts/build-herdr-ttyd.sh
```

This builds the current checkout and installs `~/.local/bin/ttyd-herdr-auth`.
`HERDR_WEB_BUILD` and `HERDR_WEB_BIN` override the build and installation paths.
The launcher prefers this separate binary; `HERDR_WEB_TTYD` can select an exact
binary path. Without either, it falls back to an installed/official ttyd, which
may lack the authentication fix.

When changing frontend source, regenerate the embedded page before building C:

```sh
cd html
corepack yarn install --immutable
corepack yarn build
cd ..
bash scripts/build-herdr-ttyd.sh
```

Windows builds use the repository's existing CMake/MSVC or cross-build setup;
the authentication change is in shared C code. After compiling, select the
resulting executable explicitly, for example from the repository root:

```powershell
$env:HERDR_WEB_TTYD = (Resolve-Path '.\build\Release\ttyd.exe').Path
powershell -NoProfile -ExecutionPolicy Bypass -File .\tun.ps1 default
```

Alternatively, install the native Windows build as
`$HOME\.local\bin\ttyd-herdr-auth.exe`. A macOS binary cannot be used on Windows
or Linux. No prebuilt executables are tracked in this repository.

## Start and manage a tunnel

Keep `tun.sh` and `tun.ps1` together when using Git Bash. They can also be copied
to your home directory. On Linux/macOS, from this repository:

```sh
bash ./tun.sh list
bash ./tun.sh default
bash ./tun.sh status
bash ./tun.sh stop
```

On native Windows, use PowerShell 5.1+ on Windows 10 1809+ or Windows 11:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tun.ps1 list
powershell -NoProfile -ExecutionPolicy Bypass -File .\tun.ps1 default
powershell -NoProfile -ExecutionPolicy Bypass -File .\tun.ps1 status
powershell -NoProfile -ExecutionPolicy Bypass -File .\tun.ps1 stop
```

Replace `default` with a named session. Without a name the launcher uses the host
name; `default` attaches the unnamed default Herdr session. A Herdr client starts
only after the browser authenticates and connects. `list` only queries sessions.

Linux/macOS run the supervisor with `nohup` in its own session/process group.
Windows uses detached processes from a local PowerShell terminal; launching from
an SSH login to Windows has not been tested. Git Bash delegates to `tun.ps1`;
WSL runs `tun.sh` and uses the WSL Herdr session, independently of native Windows.
Windows ARM64 uses x64 programs and requires OS support for x64 emulation.

Linux requires bash, curl, flock, perl, gzip and a SHA-256 utility. macOS uses
Homebrew for missing runtime dependencies. Windows installs missing tools into
the user profile without requiring WSL or an administrator. All platforms must
use a patched ttyd build for the authentication fix.

Settings: `HERDR_WEB_PORT` (default `7681`), `HERDR_WEB_USER` (default `herdr`),
`HERDR_WEB_BIN`, `HERDR_WEB_DATA`, `HERDR_WEB_STATE` and `HERDR_WEB_TTYD`.
State is isolated by host, which also avoids sharing PID files across NFS hosts.
`status` checks local processes and HTTP; it does not prove public reachability.
Restarting generates a new random public URL and password. Sleep/shutdown stops
connectivity.

## Regression checks

The tests below require Python 3.9+ and run on macOS/Linux. They use local HTTP,
WebSocket and PTY fixtures and do not create public tunnels or attach to real
Herdr sessions. The native macOS test additionally needs ttyd/flock and downloads
the pinned frontend if it is not already present in its isolated test directory.

```sh
python3 tests/test_ttyd_auth.py build/herdr-auth/ttyd
python3 tests/test_image_upload.py build/herdr-auth/ttyd
python3 tests/test_herdr_web_tunnel.py
python3 tests/test_herdr_web_tunnel.py --mac --native-lock
HERDR_WEB_TTYD="$PWD/build/herdr-auth/ttyd" python3 tests/test_herdr_web_native_mac.py
python3 tests/test_herdr_web_powershell.py
```

The PowerShell fixture requires `pwsh` (or `HERDR_TEST_PWSH`) on macOS/Linux.
It checks lifecycle, locking, quoting and process cleanup using local doubles.
It does not establish native Windows ConPTY/CIM/ACL correctness. Native Windows
runtime testing and an actual iPhone browser test are still required.

`test_image_upload.py` checks authenticated/chunked transfers, file permissions,
UTF-8 and quoted paths, size limits, cancellation, disconnects, timeouts and
read-only/disabled behavior. `image_paste_browser_fixture.py` and
`test_image_paste_browser.js` exercise real Chromium clipboard paste, ordinary
text paste, macOS Ctrl+V, raw image paths with bracketed-paste framing, file
selection, failures and reconnects. The browser
fixture defaults to a fake local PTY. Its explicit `--herdr` option attaches to
the existing default session for manual end-to-end checks; do not use it against
someone's session without permission.

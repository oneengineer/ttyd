# Building and releasing ttyd

The `ttyd binaries and release` workflow builds standalone ttyd:

- `ttyd.linux-x86_64`: Linux / Ubuntu on Intel and AMD 64-bit CPUs.
- `ttyd.linux-aarch64`: Linux / Ubuntu on ARM64 CPUs.
- `ttyd.macos-arm64`: Apple Silicon, macOS 15 or newer.
- `ttyd.windows-x86_64.exe`: native Windows x64, Windows 10 1809 or newer / Windows 11.

The web frontend is embedded. Third-party libraries are static, so the release
binaries do not need Homebrew or distribution packages at runtime. macOS system
libraries remain dynamic. macOS binaries use ad-hoc signing, not Apple Developer
ID signing or notarization.

## Build without publishing

Push a `release/**` branch or use Actions → ttyd binaries and release → Run
workflow. Download the binaries from the run's Artifacts section. Branch pushes
and manual runs do not create a release.

The workflow rebuilds the embedded frontend from the tagged source. Changes to
the frontend or tests also trigger branch builds. Release tags are validated
before dependency builds start.

Real HTTP authentication, WebSocket token authentication, PTY input/output,
rejected requests, the unauthenticated timeout, child cleanup and image upload
tests run on native Linux x86_64, Linux ARM64, macOS ARM64 and Windows x64.
The ARM64 Linux binary is cross-compiled on x86_64, then tested on an ARM64 runner
without QEMU. Windows uses native ConPTY. The `.exe` is also checked for third-party DLL and Visual C++
redistributable dependencies, and tested after copying it to an empty directory.
Windows dependencies and their licenses use a separate pinned vcpkg manifest;
the Windows dependency archive records the exact installed versions.

Every native target also runs Chromium against its standalone binary. Browser
tests cover real clipboard image and text paste, 64 KiB text, a PNG spanning
multiple upload chunks, server-side file checksums, failed uploads, reconnects,
and emulated phone/tablet fonts. These are browser automation tests, not tests
on physical iOS/Android devices or vision-model tests.

Optional WebSocket compression is disabled. libwebsockets 4.4/4.5 can disconnect
large compressed browser pastes with `rx buffer underflow` (upstream ttyd issue
[#1532](https://github.com/tsl0922/ttyd/issues/1532)). HTTP gzip and TLS remain
enabled; terminal traffic may use more bandwidth. The regression test must
reject the former 4.5.7 release binary and pass with the fixed binary.

## Publish a release

Commit and push the changes, then use a fresh `image-vX.Y.Z` feature tag:

```bash
git tag -a image-v1.0.1 -m "Fix browser image paste and standalone releases"
git push origin image-v1.0.1
```

The tag triggers all four builds. Only after every build and test succeeds does
the publisher create a draft, attach all binaries, dependency licenses and
SHA256SUMS, then publish it. The publisher uses the repository's automatic
GITHUB_TOKEN with `contents: write`; no personal token or repository secret is
required by the workflow. The operator pushing workflow changes needs GitHub
write access and permission to update workflows.

A tag matching the CMake project version followed by `-herdr.N`, such as
`1.7.7-herdr.3`, is also accepted. `image-v1.0.0` was an unsuccessful earlier
release attempt; use a new tag pointing to the repaired source rather than
moving an existing tag.

A retry can complete an existing draft. Published assets are not overwritten;
use a new numbered tag for a later release. Version validation rejects plain
upstream tags such as `1.7.7`.

## Local Mac build

```bash
bash scripts/release/build-macos.sh
python3 scripts/release/test-auth.py dist/ttyd.macos-arm64
python3 tests/test_image_upload.py dist/ttyd.macos-arm64
npm ci --prefix tests/browser
cd tests/browser && npx playwright install chromium && cd ../..
python3 scripts/release/test-browser.py dist/ttyd.macos-arm64
```

Requires Xcode command-line tools, CMake, curl, Perl and Python 3. Build files
and source downloads are placed under TMPDIR by default; set TTYD_RELEASE_BUILD
to keep them in another directory. No root access is used.

Browser tests also require native Node.js 20 or newer. To use an already
installed Chrome instead of downloading Chromium, pass `--channel chrome` to
`test-browser.py`. All tests use private loopback services and fake PTY clients;
they do not attach to Herdr or send prompts to Claude/Codex.

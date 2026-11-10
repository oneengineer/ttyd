# Building and releasing ttyd

The `ttyd binaries and release` workflow builds standalone ttyd:

- `ttyd.linux-x86_64`: Linux / Ubuntu on Intel and AMD 64-bit CPUs.
- `ttyd.linux-aarch64`: Linux / Ubuntu on ARM64 CPUs.
- `ttyd.macos-arm64`: Apple Silicon, macOS 15 or newer.

The web frontend is embedded. Third-party libraries are static, so the release
binaries do not need Homebrew or distribution packages at runtime. macOS system
libraries remain dynamic. macOS binaries use ad-hoc signing, not Apple Developer
ID signing or notarization.

## Build without publishing

Push a `release/**` branch or use Actions → ttyd binaries and release → Run
workflow. Download the binaries from the run's Artifacts section. Branch pushes
and manual runs do not create a release.

The workflow tests real HTTP authentication, WebSocket token authentication,
PTY input/output, rejected requests, the unauthenticated timeout, and child
cleanup on Linux x86_64 and macOS ARM64. Linux ARM64 is built statically and its
version command is executed using QEMU; that is not a native ARM64 PTY test.

## Publish a release

Use a fresh tag matching the CMake project version followed by `-herdr.N`:

```bash
git tag 1.7.7-herdr.1
git push origin 1.7.7-herdr.1
```

The tag triggers all three builds. Only after every build and test succeeds does
the publisher create a draft, attach all binaries, dependency licenses and
SHA256SUMS, then publish it. The publisher uses the repository's automatic
GITHUB_TOKEN with `contents: write`; no personal token or repository secret is
required by the workflow. The operator pushing workflow changes needs GitHub
write access and permission to update workflows.

A retry can complete an existing draft. Published assets are not overwritten;
use a new numbered tag for a later release. Version validation rejects an
upstream-looking tag without the fork suffix.

## Local Mac build

```bash
bash scripts/release/build-macos.sh
python3 scripts/release/test-auth.py dist/ttyd.macos-arm64
```

Requires Xcode command-line tools, CMake, curl, Perl and Python 3. Build files
and source downloads are placed under TMPDIR by default; set TTYD_RELEASE_BUILD
to keep them in another directory. No root access is used.

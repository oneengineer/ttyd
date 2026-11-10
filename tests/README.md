# Regression tests

See [Herdr web terminal](../docs/herdr-web.md#regression-checks) for commands and
platform requirements.

- `test_ttyd_auth.py`: real ttyd HTTP, WebSocket and PTY checks for authenticated
  headerless connections, input, disconnect cleanup, incorrect headers/tokens,
  malformed/empty initial frames, authentication timeout and token log redaction.
- `test_image_upload.py`: actual binary image transfers, UTF-8/quoted paths,
  private file permissions, size limits, expiry, cancellation, disconnect cleanup,
  stalled-upload timeout, and writable/authentication/opt-in checks.
- `image_paste_browser_fixture.py` and `test_image_paste_browser.js`: real
  Chromium clipboard and file-selection checks with a fake local PTY. The fixture's
  explicit `--herdr` option instead attaches to a real default Herdr session and
  requires permission to operate that session; it is not part of the automated suite.
- `test_herdr_web_tunnel.py`: Linux/macOS launcher lifecycle using local doubles
  and real OS locks, including detached-session survival, duplicate startup,
  stale process identities and preservation of unrelated work.
- `test_herdr_web_native_mac.py`: real macOS ttyd/WebSocket client disconnects
  with fake Herdr/cloudflared commands.
- `test_herdr_web_powershell.py`: PowerShell lifecycle, argument quoting and
  cleanup with local doubles; native Windows behavior is not emulated as proven.
- `herdr-web-fixture.py`: shared local-only command doubles.

Each test creates an isolated temporary state directory. It does not operate on
the launcher's normal state or open a public tunnel.

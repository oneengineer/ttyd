#!/usr/bin/env python3
"""Test a standalone binary with a real browser and private PTY; never attach Herdr."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('binary', type=Path)
parser.add_argument('--node', default='node')
parser.add_argument('--channel', default='', help='Optional installed Chromium channel, e.g. chrome')
args = parser.parse_args()
repo = Path(__file__).resolve().parents[2]
root = Path(tempfile.mkdtemp(prefix='ttyd-browser-test.', dir=os.environ.get('RUNNER_TEMP')))
print('Browser test logs:', root, flush=True)
aliases = {'aarch64': 'arm64', 'amd64': 'x64', 'x86_64': 'x64'}
native = platform.machine().lower()
native = aliases.get(native, native)
node_arch = subprocess.check_output([args.node, '-p', 'process.arch'], text=True).strip()
if node_arch != native:
    raise SystemExit(f'Node must run natively: Python={native}, Node={node_arch}')

images = root / "images with 'quotes 中文"
images.mkdir(mode=0o700)
trace = root / 'pty-input'
trace.touch()
client = root / 'client.py'
client.write_text('''import os,sys
if os.name == 'nt':
 import ctypes,msvcrt
 from ctypes import wintypes
 kernel = ctypes.WinDLL('kernel32', use_last_error=True)
 kernel.GetStdHandle.argtypes = [wintypes.DWORD]
 kernel.GetStdHandle.restype = wintypes.HANDLE
 kernel.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
 msvcrt.setmode(0, os.O_BINARY)
 msvcrt.setmode(1, os.O_BINARY)
 kernel.SetConsoleCP(65001)
 kernel.SetConsoleOutputCP(65001)
 if not kernel.SetConsoleMode(kernel.GetStdHandle(-10), 0x0200):
  raise ctypes.WinError(ctypes.get_last_error())
 if not kernel.SetConsoleMode(kernel.GetStdHandle(-11), 0x0005):
  raise ctypes.WinError(ctypes.get_last_error())
else:
 import tty
 tty.setraw(0)
os.write(1,b'\\x1b[?2004hBROWSER-PTY-READY\\r\\n')
while True:
 data=os.read(0,4096)
 if not data: break
 with open(sys.argv[1], 'ab') as f: f.write(data)
 os.write(1,b'INPUT:'+data.hex().encode()+b'\\r\\n')
''', encoding='utf-8')
with socket.socket() as sock:
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
url = f'http://127.0.0.1:{port}'
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
log = (root / 'ttyd.log').open('wb')
server = subprocess.Popen([
    str(args.binary.resolve()), '-i', '127.0.0.1', '-p', str(port), '-W', '-s', '15',
    '-c', 'image-test:local-only-test', '--image-upload', '--image-upload-dir', str(images),
    sys.executable, str(client), str(trace),
], stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=os.name != 'nt')
try:
    deadline = time.monotonic() + 10
    while True:
        if server.poll() is not None:
            raise RuntimeError('ttyd exited: ' + (root / 'ttyd.log').read_text(errors='replace'))
        try:
            opener.open(url, timeout=1).close()
        except urllib.error.HTTPError as error:
            if error.code == 401:
                break
            raise
        except urllib.error.URLError:
            pass
        if time.monotonic() > deadline:
            raise TimeoutError('ttyd did not start')
        time.sleep(.05)
    report = root / 'browser.json'
    subprocess.run([args.node, str(repo / 'tests/browser/run.cjs'), url, str(report), args.channel],
                   cwd=repo, check=True, timeout=150)
    result = json.loads(report.read_text(encoding='utf-8'))
    for image in result['paste']['imageFiles']:
        saved = Path(image['path'])
        assert saved.parent.parent == images.resolve(), saved
        assert hashlib.sha256(saved.read_bytes()).hexdigest() == image['sha256'], saved
    raw = trace.read_bytes()
    assert b'LONG-PASTE-' + b'x' * 65536 + b'-END' in raw, 'Long paste did not reach the PTY intact'
    assert b'AFTER-RECONNECT' in raw, 'PTY was not responsive after reconnect'
    assert 'rx buffer underflow' not in (root / 'ttyd.log').read_text(errors='replace')
    print('PASS: server PNG checksums and actual PTY input match browser data', flush=True)
finally:
    if server.poll() is None:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait()
    log.close()
    if sys.exc_info()[0] is not None:
        print('ttyd failure log:\n' + (root / 'ttyd.log').read_text(encoding='utf-8', errors='replace')[-12000:],
              file=sys.stderr, flush=True)

#!/usr/bin/env python3
"""Loopback-only browser fixture. --herdr explicitly attaches the existing default session."""
import argparse
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('binary', type=Path)
parser.add_argument('--herdr', action='store_true')
parser.add_argument('--port', type=int, default=0, help='Reuse a loopback URL for before/after browser checks')
parser.add_argument('--system-tmp', action='store_true', help='Use ttyd\'s default image directory, as the launchers do')
args = parser.parse_args()
root = Path(tempfile.mkdtemp(prefix='ttyd-image-browser.'))
images = Path(tempfile.gettempdir()) if args.system_tmp else root / "images with 'quotes 中文"
if not args.system_tmp:
    images.mkdir(mode=0o700)
trace = root / 'pty-input'
trace.touch()
client = root / 'client.py'
client.write_text('''import os,sys,tty
tty.setraw(0)
os.write(1,b'\\x1b[?2004hBROWSER-PTY-READY\\r\\n')
while True:
 data=os.read(0,4096)
 if not data: break
 with open(sys.argv[1], 'ab') as f: f.write(data)
 os.write(1,b'INPUT:'+data.hex().encode()+b'\\r\\n')
''')
with socket.socket() as sock:
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(('127.0.0.1', args.port))
    port = sock.getsockname()[1]
repo = Path(__file__).resolve().parents[1]
command = (['bash', str(repo / 'tun.sh'), '__web_client', '/opt/homebrew/bin/herdr']
           if args.herdr else [sys.executable, str(client), str(trace)])
log = (root / 'ttyd.log').open('wb')
image_options = [] if args.system_tmp else ['--image-upload-dir', str(images)]
proc = subprocess.Popen([str(args.binary.resolve()), '-i', '127.0.0.1', '-p', str(port),
                         '-W', '-s', '15', '--image-upload', *image_options,
                         '-c', 'image-test:local-only-test', '-t', 'scrollback=10000', *command],
                        stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                        cwd=Path.home() if args.herdr else root, start_new_session=True)
state = dict(pid=os.getpid(), ttyd_pid=proc.pid, url=f'http://127.0.0.1:{port}',
             root=str(root), images=str(images), trace=str(trace), herdr=args.herdr)
state_file = Path('/tmp/ttyd-image-browser-fixture.json')
state_file.write_text(json.dumps(state))
state_file.chmod(0o600)
print(json.dumps(state), flush=True)


def stop(*_):
    raise SystemExit(0)


signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)
try:
    while proc.poll() is None:
        time.sleep(.2)
finally:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    log.close()

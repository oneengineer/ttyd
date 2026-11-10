#!/usr/bin/env python3
"""Authenticated real WS/PTY/file tests; no Herdr, agents or public tunnels."""
import base64
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

from ttyd_ws import WS, wait_for

BINARY = Path(sys.argv[1]).resolve()
ROOT = Path(tempfile.mkdtemp(prefix='ttyd-image-test.'))
IMAGES = ROOT / "images with 'quotes 中文"
IMAGES.mkdir(mode=0o700)
TRACE = ROOT / 'pty-input'
TRACE.touch()
FIXTURE = ROOT / 'client.py'
FIXTURE.write_text('''import os,sys
if os.name != 'nt':
 import tty
 tty.setraw(0)
os.write(1,b'IMAGE-PTY-READY\\r\\n')
while True:
 data=os.read(0,4096)
 if not data: break
 with open(sys.argv[1], 'ab') as f: f.write(data)
 os.write(1,b'PTY-ECHO:'+data)
''')
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jWZkAAAAASUVORK5CYII=')
TOKEN = base64.b64encode(b'local-image-test:local-only-password').decode()
SERVERS = []
SOCKETS = []


def start(*extra):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    log = (ROOT / f'ttyd-{port}.log').open('wb')
    proc = subprocess.Popen([str(BINARY), '-i', '127.0.0.1', '-p', str(port), '-s', '15',
                             '-c', 'local-image-test:local-only-password', '--image-upload-dir', str(IMAGES),
                             *extra, sys.executable, str(FIXTURE), str(TRACE)],
                            stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    SERVERS.append((proc, log))
    def ready():
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=.1):
                return True
        except OSError:
            assert proc.poll() is None, (ROOT / f'ttyd-{port}.log').read_text()
            return False
    wait_for(ready)
    return port


def connect(port):
    ws = WS(port)
    SOCKETS.append(ws)
    prefs = ws.login(TOKEN)
    while b'IMAGE-PTY-READY' not in ws.output:
        ws.expect('0')
    return ws, prefs


def request(ws, command, data=b'', expected=None):
    ws.send(command.encode() + data)
    reply = json.loads(ws.expect('3'))
    if expected:
        assert reply['status'] == expected, reply
    return reply


def begin(ws, size=len(PNG), mime='image/png', request_id='test-image'):
    return request(ws, '4', json.dumps(dict(id=request_id, type=mime, size=size)).encode())


def upload(ws, data, mime='image/png'):
    assert begin(ws, len(data), mime)['status'] == 'ready'
    for offset in range(0, len(data), 65536):
        request(ws, '5', data[offset:offset + 65536], 'chunk')
    return Path(request(ws, '6', expected='complete')['path'])


def incomplete_cleaned():
    return set(IMAGES.iterdir()) == known


try:
    # Expiration only touches generated private directories; fresh data and links survive.
    expired = IMAGES / 'ttyd-image-old123'
    expired.mkdir(mode=0o700)
    (expired / 'image.png').write_bytes(PNG)
    old = time.time() - 90000
    os.utime(expired, (old, old))
    fresh = IMAGES / 'ttyd-image-new123'
    fresh.mkdir(mode=0o700)
    (fresh / 'image.png').write_bytes(PNG)
    link = IMAGES / 'ttyd-image-link12'
    link.symlink_to(fresh, target_is_directory=True)
    port = start('-W', '--image-upload')
    ws, prefs = connect(port)
    assert prefs['imageUpload'] is True and prefs['imageUploadMaxBytes'] == 20 * 1024 * 1024
    one = upload(ws, PNG)
    assert one.is_absolute() and one.read_bytes() == PNG
    assert one.parent.parent == IMAGES.resolve()
    assert not expired.exists() and fresh.exists() and link.is_symlink()
    if os.name != 'nt':
        assert stat.S_IMODE(one.stat().st_mode) == 0o600
        assert stat.S_IMODE(one.parent.stat().st_mode) == 0o700
    assert TRACE.read_bytes() == b'', 'Backend must never write image bytes, paths or Enter to the PTY'
    print('PASS: headerless authenticated upload, private UTF-8 paths, expiry, no PTY input', flush=True)

    large = PNG + os.urandom(180000)
    saved = upload(ws, large)
    assert hashlib.sha256(saved.read_bytes()).digest() == hashlib.sha256(large).digest()
    assert begin(ws)['status'] == 'ready'
    ws.send(b'5' + PNG[:10], final=False)
    ws.send(PNG[10:], opcode=0)
    assert json.loads(ws.expect('3'))['status'] == 'chunk'
    fragmented = Path(request(ws, '6', expected='complete')['path'])
    assert fragmented.read_bytes() == PNG
    print('PASS: binary multi-chunk transfer and fragmented WebSocket messages preserve exact bytes', flush=True)
    known = set(IMAGES.iterdir())

    for size, mime in ((0, 'image/png'), (-1, 'image/png'), (20971521, 'image/png'), (10, 'text/plain')):
        assert begin(ws, size, mime)['status'] == 'error'
        assert incomplete_cleaned()
    for payload in (b'{', b'{}', b'{"id":1,"type":"image/png","size":10}',
                    b'{"id":"bad\\u0000id","type":"image/png","size":10}'):
        request(ws, '4', payload, 'error')
        assert incomplete_cleaned()
    request(ws, '5', PNG, 'error')
    for declared, data in ((len(PNG), b'x' * len(PNG)), (len(PNG) + 1, PNG)):
        assert begin(ws, declared)['status'] == 'ready'
        request(ws, '5', data, 'chunk')
        request(ws, '6', expected='error')
        assert incomplete_cleaned()
    assert begin(ws, 3)['status'] == 'ready'
    request(ws, '5', PNG, 'error')
    assert incomplete_cleaned()
    print('PASS: malformed metadata, wrong contents, missing/extra bytes and size limits leave no partial files', flush=True)

    assert begin(ws)['status'] == 'ready'
    request(ws, '5', PNG[:10], 'chunk')
    request(ws, '7', expected='cancelled')
    assert incomplete_cleaned()
    assert begin(ws)['status'] == 'ready'
    ws.close()
    SOCKETS.remove(ws)
    wait_for(incomplete_cleaned)
    assert one.exists() and saved.exists() and fragmented.exists()
    ws, _ = connect(port)
    assert begin(ws, 100000)['status'] == 'ready'
    ws.send(b'5' + b'x' * 65537)
    assert ws.expect('close') == 1009
    ws.close()
    SOCKETS.remove(ws)
    wait_for(incomplete_cleaned)
    print('PASS: cancellation, disconnect and oversized frames clean partial files; completed images survive', flush=True)

    unauth = WS(port)
    SOCKETS.append(unauth)
    unauth.send(b'4{"id":"no-login","type":"image/png","size":10}')
    assert unauth.expect('close') in (None, 1008)
    assert incomplete_cleaned()
    for args in (('-W',), ('--image-upload',)):
        disabled, disabled_prefs = connect(start(*args))
        assert disabled_prefs['imageUpload'] is False
        request(disabled, '4', b'{}', 'error')
        assert incomplete_cleaned()
    print('PASS: authentication, explicit opt-in and writable permission enforced by the server', flush=True)

    ws, _ = connect(port)
    ws.send(b'0NORMAL-TEXT-PASTE\r')
    wait_for(lambda: b'NORMAL-TEXT-PASTE\r' in TRACE.read_bytes())
    assert b'0' not in TRACE.read_bytes() and PNG not in TRACE.read_bytes()
    assert begin(ws)['status'] == 'ready'
    assert request(ws, '4', json.dumps(dict(id='replacement', type='image/png', size=len(PNG))).encode())['status'] == 'ready'
    request(ws, '5', PNG[:10], 'chunk')
    print('Waiting for the 30-second abandoned-upload timeout…', flush=True)
    timed_out = json.loads(ws.expect('3', seconds=33))
    assert timed_out['status'] == 'error' and 'timed out' in timed_out['error']
    assert incomplete_cleaned()
    ws.send(b'0STILL-CONNECTED')
    wait_for(lambda: b'STILL-CONNECTED' in TRACE.read_bytes())
    print('PASS: timeout/replacement cleanup and normal terminal input after upload failures', flush=True)
finally:
    for ws in SOCKETS:
        ws.close()
    for proc, log in SERVERS:
        if proc.poll() is None:
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        log.close()
    print('Logs and uploaded fixtures:', ROOT, flush=True)

assert one.exists(), 'Completed image should survive a ttyd restart'
print('All image upload protocol checks passed.', flush=True)

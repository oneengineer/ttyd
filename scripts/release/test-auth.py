#!/usr/bin/env python3
"""Real HTTP/WebSocket/PTY authentication tests. No Herdr or public tunnel."""
import base64
import json
import os
from pathlib import Path
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

BINARY = Path(sys.argv[1]).resolve()
ROOT = Path(tempfile.mkdtemp(prefix='ttyd-auth-test.'))
TRACE = ROOT / 'clients'
TRACE.touch()
FIXTURE = ROOT / 'client.py'
FIXTURE.write_text('''import os,sys
with open(sys.argv[1], 'a') as f: f.write(str(os.getpid())+'\\n')
os.write(1,b'AUTHENTICATED-PTY-READY\\r\\n')
while True:
    data=os.read(0,4096)
    if not data: break
    os.write(1,b'PTY-ECHO:'+data)
''')
CREDENTIAL = 'test:' + os.urandom(16).hex()
TOKEN = base64.b64encode(CREDENTIAL.encode()).decode()
with socket.socket() as s:
    s.bind(('127.0.0.1', 0))
    PORT = s.getsockname()[1]
LOG = (ROOT / 'ttyd.log').open('wb')
SERVER = subprocess.Popen([str(BINARY), '-i', '127.0.0.1', '-p', str(PORT), '-W', '-s', '15',
                           '-c', CREDENTIAL, sys.executable, str(FIXTURE), str(TRACE)],
                          stdin=subprocess.DEVNULL, stdout=LOG, stderr=LOG, start_new_session=True)
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def http(path, authorized=False):
    headers = {'Authorization': 'Basic ' + TOKEN} if authorized else {}
    try:
        with OPENER.open(urllib.request.Request(f'http://127.0.0.1:{PORT}{path}', headers=headers), timeout=3) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except urllib.error.URLError:
        return 0, b''


def clients():
    return [int(p) for p in TRACE.read_text().splitlines()]


def alive(pid):
    p = subprocess.run(['ps', '-p', str(pid), '-o', 'stat=,rss=,vsz='], capture_output=True, text=True)
    if not p.stdout.strip():
        return False
    state, rss, vsz = p.stdout.split()
    return 'Z' not in state and not ('E' in state and int(rss) == int(vsz) == 0)


def wait_for(predicate, seconds=4):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.03)
    raise AssertionError('Timed out waiting for expected process state')


class WS:
    def __init__(self, authorization=None):
        self.sock = socket.create_connection(('127.0.0.1', PORT), timeout=3)
        self.pending = b''
        key = base64.b64encode(os.urandom(16)).decode()
        headers = (f'GET /ws HTTP/1.1\r\nHost: 127.0.0.1:{PORT}\r\n'
                   'Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Version: 13\r\n'
                   f'Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Protocol: tty\r\n')
        if authorization is not None:
            headers += f'Authorization: {authorization}\r\n'
        self.sock.sendall((headers + '\r\n').encode())
        response = b''
        while b'\r\n\r\n' not in response:
            chunk = self.sock.recv(8192)
            if not chunk:
                break
            response += chunk
        header, _, self.pending = response.partition(b'\r\n\r\n')
        self.open = header.startswith(b'HTTP/1.1 101 ')

    def send(self, payload, opcode=2):
        mask = os.urandom(4)
        n = len(payload)
        header = bytes([0x80 | opcode])
        header += bytes([0x80 | n]) if n < 126 else b'\xfe' + struct.pack('!H', n)
        self.sock.sendall(header + mask + bytes(c ^ mask[i % 4] for i, c in enumerate(payload)))

    def exact(self, n):
        while len(self.pending) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise EOFError
            self.pending += chunk
        data, self.pending = self.pending[:n], self.pending[n:]
        return data

    def observe(self, seconds, stop_at=None):
        deadline = time.monotonic() + seconds
        result = {'data': b'', 'closed': False, 'code': None}
        while time.monotonic() < deadline:
            self.sock.settimeout(max(.01, deadline - time.monotonic()))
            try:
                first, second = self.exact(2)
                length = second & 127
                if length == 126:
                    length = struct.unpack('!H', self.exact(2))[0]
                elif length == 127:
                    length = struct.unpack('!Q', self.exact(8))[0]
                payload = self.exact(length)
            except socket.timeout:
                break
            except EOFError:
                result['closed'] = True
                break
            opcode = first & 15
            if opcode == 9:
                self.send(payload, 10)
            elif opcode == 8:
                result['closed'] = True
                result['code'] = struct.unpack('!H', payload[:2])[0] if len(payload) >= 2 else None
                break
            elif opcode in (1, 2):
                result['data'] += payload
                if stop_at and stop_at in result['data']:
                    break
        return result

    def close(self):
        if self.open:
            try:
                self.send(struct.pack('!H', 1000), 8)
            except OSError:
                pass
        self.sock.close()


try:
    wait_for(lambda: SERVER.poll() is not None or http('/', True)[0] == 200)
    assert SERVER.poll() is None
    assert http('/')[0] == http('/token')[0] == 401
    code, token_json = http('/token', True)
    assert code == 200 and json.loads(token_json)['token'] == TOKEN
    print('PASS: HTTP page/token still require the password', flush=True)

    # The real phone symptom: no Authorization header, but a valid token from HTTP.
    for attempt, authorization in enumerate((None, 'Basic ' + TOKEN, None, None, None), 1):
        count = len(clients())
        ws = WS(authorization)
        try:
            assert ws.open
            before = ws.observe(.15)
            assert not before['data'] and not before['closed'] and len(clients()) == count
            ws.send(json.dumps({'AuthToken': TOKEN, 'columns': 80, 'rows': 24}).encode())
            out = ws.observe(3, b'AUTHENTICATED-PTY-READY')
            assert b'AUTHENTICATED-PTY-READY' in out['data'], out
            wait_for(lambda: len(clients()) == count + 1)
            pid = clients()[-1]
            assert alive(pid)
            ws.send(b'0hello-test\r')
            assert b'PTY-ECHO:hello-test' in ws.observe(3, b'PTY-ECHO:hello-test')['data']
        finally:
            ws.close()
        wait_for(lambda: not alive(pid))
    print('PASS: missing-header token login, normal desktop login, PTY input and five disconnects without leaks', flush=True)

    for authorization in ('Basic incorrect', 'x', 'Basic ' + 'x' * 1024):
        count = len(clients())
        ws = WS(authorization)
        try:
            assert not ws.open and len(clients()) == count
        finally:
            ws.close()
    print('PASS: wrong, malformed and oversized Authorization headers rejected', flush=True)

    for payload in (b'{"AuthToken":"WRONG-SECRET-DO-NOT-LOG"}', b'{}', b'{', b'0unauthenticated-input', b''):
        count = len(clients())
        ws = WS()
        try:
            assert ws.open
            ws.send(payload)
            outcome = ws.observe(3)
            assert outcome['closed'] and not outcome['data'] and len(clients()) == count, outcome
            assert SERVER.poll() is None
        finally:
            ws.close()
    print('PASS: wrong/missing token, malformed JSON, unauthenticated input and empty frames cannot start a process', flush=True)

    ws = WS('Basic ' + TOKEN)
    try:
        ws.send(b'{"AuthToken":"wrong-even-with-valid-header"}')
        outcome = ws.observe(3)
        assert outcome['closed'] and not outcome['data']
    finally:
        ws.close()
    print('PASS: a valid HTTP header does not bypass first-message token verification', flush=True)

    ws = WS()
    try:
        count, started = len(clients()), time.monotonic()
        outcome = ws.observe(12)
        assert outcome['closed'] and not outcome['data'] and len(clients()) == count
        assert 9 <= time.monotonic() - started <= 12
    finally:
        ws.close()
    log = (ROOT / 'ttyd.log').read_text(errors='replace')
    assert 'WS authentication timed out' in log
    assert 'WRONG-SECRET-DO-NOT-LOG' not in log
    assert SERVER.poll() is None
    print('PASS: unauthenticated connections expire after 10 seconds; rejected tokens are not logged', flush=True)
finally:
    SERVER.terminate()
    SERVER.wait(timeout=5)
    LOG.close()
    for pid in clients():
        if alive(pid):
            args = subprocess.run(['ps', '-p', str(pid), '-o', 'args='], capture_output=True, text=True).stdout
            if str(FIXTURE) in args:
                os.kill(pid, signal.SIGKILL)
    print('Auth test logs:', ROOT, flush=True)

#!/usr/bin/env python3
"""Real macOS ttyd/HTTP/WebSocket/locks; fake Herdr and CF, no public tunnel."""
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
import urllib.request

SCRIPT = Path(__file__).resolve().parents[1] / 'tun.sh'
FIXTURE = Path(__file__).with_name('herdr-web-fixture.py')
root = Path(tempfile.mkdtemp(prefix='herdr-web-native-mac.'))
state, binaries, data = (root / name for name in ('state', 'bin', 'data'))
for directory in (state, binaries, data):
    directory.mkdir()
trace = root / 'trace.jsonl'
trace.touch()
for name in ('herdr', 'cloudflared'):
    binary = binaries / name
    binary.write_text('#!' + sys.executable + '\n' + FIXTURE.read_text().split('\n', 1)[1])
    binary.chmod(0o755)
with socket.socket() as sock:
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
env = dict(os.environ, PATH=f'{binaries}:/usr/bin:/bin:/opt/homebrew/bin:/usr/local/bin',
           HERDR_WEB_STATE=str(state), HERDR_WEB_BIN=str(binaries), HERDR_WEB_DATA=str(data),
           HERDR_WEB_PORT=str(port), HERDR_WEB_TEST_TRACE=str(trace), HERDR_WEB_TEST_DIR=str(root),
           HERDR_SESSION='inherited-wrong', HERDR_SOCKET_PATH='/wrong/socket', HERDR_ENV='1')
agent = subprocess.Popen([sys.executable, str(FIXTURE), 'agent'], env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)

def run(*args, ok=True):
    result = subprocess.run(['/bin/bash', str(SCRIPT), *args], env=env, text=True,
                            capture_output=True, timeout=120)
    if ok and result.returncode:
        raise AssertionError(result.stdout + result.stderr)
    return result

def records(event):
    return [r for line in trace.read_text().splitlines() if (r := json.loads(line))['event'] == event]

def live(pid):
    result = subprocess.run(['ps', '-p', str(pid), '-o', 'stat=,rss=,vsz='], capture_output=True, text=True)
    if result.returncode or not result.stdout.strip():
        return False
    state, rss, vsz = result.stdout.split()
    # Darwin may retain an exiting kernel record even though both resident and
    # virtual memory are already zero. It is not a blocked/running Herdr client.
    if 'E' in state and int(rss) == 0 and int(vsz) == 0:
        return False
    return 'Z' not in state

def wait_for(predicate):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError('Timed out')

def send_frame(sock, payload, opcode=2):
    mask = os.urandom(4)
    length = len(payload)
    header = bytes([0x80 | opcode])
    if length < 126:
        header += bytes([0x80 | length])
    else:
        header += bytes([0x80 | 126]) + struct.pack('!H', length)
    sock.sendall(header + mask + bytes(value ^ mask[i % 4] for i, value in enumerate(payload)))

try:
    started = run('native-mac-test')
    assert '用户名：herdr' in started.stdout and '密码：' in started.stdout
    values = dict(line.split('=', 1) for line in (state / 'instance.env').read_text().splitlines())
    authorization = 'Basic ' + base64.b64encode((values['username'] + ':' + values['password']).encode()).decode()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(f'http://127.0.0.1:{port}/token', headers={'Authorization': authorization})
    with opener.open(req, timeout=5) as response:
        token = json.load(response)['token']
    binary_help = subprocess.run([values['ttyd_path'], '--help'], capture_output=True, text=True)
    image_capable = '--image-upload' in binary_help.stdout + binary_help.stderr
    legacy_frontend = data / 'ttyd-2922cb89f518bae4d0fcf4d757a7419638fc71fc.index.html'
    if image_capable:
        assert not legacy_frontend.exists(), 'Do not replace the embedded image frontend with old HTML'
        req = urllib.request.Request(f'http://127.0.0.1:{port}/', headers={'Authorization': authorization})
        with opener.open(req, timeout=5) as response:
            assert b'ttyd-image-upload' in response.read()
    else:
        assert legacy_frontend.stat().st_size > 10000
    assert 'native-mac-test' in run('status').stdout
    assert run('second', ok=False).returncode
    worker_pid = int((state / 'instance.lock').read_text())
    ttyd_pid = int((state / 'ttyd.pid').read_text())
    os.kill(worker_pid, signal.SIGHUP)
    assert live(worker_pid) and live(ttyd_pid)
    print('PASS: real Darwin arm64 ttyd, matching frontend, auth, native locks, SIGHUP survival', flush=True)

    for attempt in range(3):
        seen = len(records('herdr'))
        ws = socket.create_connection(('127.0.0.1', port), timeout=5)
        key = base64.b64encode(os.urandom(16)).decode()
        ws.sendall((f'GET /ws HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n'
                    'Upgrade: websocket\r\nConnection: Upgrade\r\n'
                    f'Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n'
                    'Sec-WebSocket-Protocol: tty\r\n'
                    f'Origin: http://127.0.0.1:{port}\r\nAuthorization: {authorization}\r\n\r\n').encode())
        response = b''
        while b'\r\n\r\n' not in response:
            response += ws.recv(4096)
        assert b'101 Switching Protocols' in response, response[:100]
        send_frame(ws, json.dumps({'AuthToken': token, 'columns': 80, 'rows': 24}).encode())
        wait_for(lambda: len(records('herdr')) > seen)
        rec = records('herdr')[-1]
        assert rec['args'] == ['--session', 'native-mac-test'] and not rec['inherited']
        assert live(rec['pid'])
        send_frame(ws, b'', opcode=8)
        ws.close()
        wait_for(lambda: not live(rec['pid']))
        assert agent.poll() is None
        print(f'PASS: real WebSocket connect/disconnect {attempt + 1}; Herdr client exits, agent survives', flush=True)

    run('stop')
    wait_for(lambda: not live(worker_pid) and not live(ttyd_pid))
    assert agent.poll() is None and run('status', ok=False).returncode
    print('PASS: real macOS stop cleans up the server and preserves background work', flush=True)
finally:
    try:
        run('stop')
    finally:
        agent.terminate()
        agent.wait(timeout=5)
    print('Native fixture logs:', root)

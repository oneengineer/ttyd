#!/usr/bin/env python3
"""Offline Herdr/ttyd/cloudflared doubles. Never opens a public tunnel."""
import base64
import fcntl
import hashlib
import http.server
import json
import os
from pathlib import Path
import pty
import signal
import subprocess
import sys
import time

name = Path(sys.argv[0]).name.removesuffix('.exe')
trace = Path(os.environ['HERDR_WEB_TEST_TRACE'])

def record(event, **kwargs):
    with trace.open('a') as stream:
        stream.write(json.dumps(dict(event=event, pid=os.getpid(), parent=os.getppid(), **kwargs)) + '\n')

if name == 'uname':
    print(os.environ.get('HERDR_WEB_TEST_PLATFORM', 'Linux') if sys.argv[1:] == ['-s'] else 'x86_64')
elif name == 'sha256sum':
    print(hashlib.sha256(Path(sys.argv[1]).read_bytes()).hexdigest(), sys.argv[1])
elif name == 'flock':
    try:
        fcntl.flock(int(sys.argv[-1]), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit(1)
elif name == 'herdr':
    record('herdr', args=sys.argv[1:], hup_ignored=signal.getsignal(signal.SIGHUP) == signal.SIG_IGN,
           term_ignored=signal.getsignal(signal.SIGTERM) == signal.SIG_IGN, pgid=os.getpgrp(), sid=os.getsid(0),
           inherited={k: os.environ[k] for k in ('HERDR_SESSION', 'HERDR_SOCKET_PATH', 'HERDR_ENV') if k in os.environ})
    if sys.argv[1:3] == ['session', 'list']:
        sessions = [{'name': 'default', 'running': True}, {'name': 'test-session', 'running': True}]
        print(json.dumps(sessions) if '--json' in sys.argv else 'NAME          RUNNING\ndefault       true\ntest-session   true')
    else:
        # A connected client blocked writing to an unread terminal, as in the original incident.
        while True:
            os.write(sys.stdout.fileno(), b'x' * 65536)
elif name == 'cloudflared':
    record('cloudflared', args=sys.argv[1:], home=os.environ.get('HOME'))
    if Path(os.environ['HERDR_WEB_TEST_DIR'], 'fail-cloudflared').exists():
        sys.exit(7)
    print('https://offline-herdr-test.trycloudflare.com', flush=True)
    print('Registered tunnel connection', flush=True)
    while True:
        time.sleep(0.2)
elif name == 'ttyd':
    argv = sys.argv[1:]
    if argv == ['--help']:
        print('--image-upload' if os.environ.get('HERDR_WEB_TEST_IMAGE_UPLOAD') == '1' else 'ttyd fixture (stock frontend)', file=sys.stderr)
        sys.exit(0)
    port = int(argv[argv.index('-p') + 1])
    credential = argv[argv.index('-c') + 1]
    sig = int(argv[argv.index('-s') + 1]) if '-s' in argv else signal.SIGHUP
    # Bash wrapper execs Herdr after clearing inherited session variables.
    command = argv[argv.index('-c') + 2:]
    clients = []
    record('ttyd', args=argv, hup_ignored=signal.getsignal(signal.SIGHUP) == signal.SIG_IGN)

    def disconnect():
        while clients:
            proc, master = clients.pop()
            if proc.poll() is None:
                os.killpg(proc.pid, sig)
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    record('leaked-client', child=proc.pid)
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait()
            os.close(master)

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            expected = 'Basic ' + base64.b64encode(credential.encode()).decode()
            if self.headers.get('Authorization') != expected:
                self.send_response(401)
                self.end_headers()
                return
            if self.path == '/__test_connect':
                master, slave = pty.openpty()
                proc = subprocess.Popen(command, stdin=slave, stdout=slave, stderr=slave,
                                        start_new_session=True, close_fds=True)
                os.close(slave)
                clients.append((proc, master))
                body = str(proc.pid).encode()
            elif self.path == '/__test_disconnect':
                disconnect()
                body = b'closed'
            else:
                body = b'ttyd fixture'
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    def terminate(*_):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, terminate)
    server = http.server.HTTPServer(('127.0.0.1', port), Handler)
    try:
        server.serve_forever(poll_interval=0.1)
    finally:
        disconnect()
        server.server_close()
else:
    record('agent')
    while True:
        time.sleep(0.2)

#!/usr/bin/env python3
"""Lifecycle and signal regressions, with real local HTTP and OS file locks."""
import base64
import http.server
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

SCRIPT = Path(__file__).resolve().parents[1] / 'tun.sh'
FIXTURE = Path(__file__).with_name('herdr-web-fixture.py')
root = Path(tempfile.mkdtemp(prefix='herdr-web-test.'))
state, binaries, data = (root / name for name in ('state', 'bin', 'data'))
for path in (state, binaries, data):
    path.mkdir()
trace = root / 'trace.jsonl'
trace.touch()
for name in ('herdr', 'ttyd', 'cloudflared', 'uname', 'flock', 'sha256sum'):
    if name == 'flock' and '--native-lock' in sys.argv:
        continue
    shim = binaries / name
    shim.write_text('#!' + sys.executable + '\n' + FIXTURE.read_text().split('\n', 1)[1])
    shim.chmod(0o755)
(data / 'ttyd-2922cb89f518bae4d0fcf4d757a7419638fc71fc.index.html').write_text('registerOscHandler(52')
with socket.socket() as sock:
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
env = dict(os.environ, PATH=str(binaries) + os.pathsep + os.environ['PATH'],
           HERDR_WEB_TEST_PLATFORM='Darwin' if '--mac' in sys.argv else 'Linux',
           HERDR_WEB_STATE=str(state), HERDR_WEB_BIN=str(binaries), HERDR_WEB_DATA=str(data),
           HERDR_WEB_PORT=str(port), HERDR_WEB_TEST_TRACE=str(trace), HERDR_WEB_TEST_DIR=str(root),
           http_proxy='http://127.0.0.1:1', no_proxy='', NO_PROXY='',
           HERDR_SESSION='inherited-wrong-session', HERDR_SOCKET_PATH='/wrong/socket', HERDR_ENV='1')
agent = subprocess.Popen([sys.executable, str(FIXTURE), 'agent'], env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)

def run(*args, ok=True):
    launcher = subprocess.Popen(['bash', str(SCRIPT), *args], env=env, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    out, err = launcher.communicate(timeout=40)
    proc = subprocess.CompletedProcess(launcher.args, launcher.returncode, out, err)
    proc.launcher_pid = launcher.pid
    if ok and proc.returncode:
        raise AssertionError(f'{args}: {proc.stdout}\n{proc.stderr}')
    return proc

def records(event):
    return [r for line in trace.read_text().splitlines() if (r := json.loads(line))['event'] == event]

def wait_for(predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError('Timed out waiting for condition')

def live(pid):
    proc = subprocess.run(['ps', '-p', str(pid), '-o', 'stat='], capture_output=True, text=True)
    return proc.returncode == 0 and bool(proc.stdout.strip()) and 'Z' not in proc.stdout

def meta():
    return dict(line.split('=', 1) for line in (state / 'instance.env').read_text().splitlines())

def request(path):
    info = meta()
    credential = base64.b64encode((info['username'] + ':' + info['password']).encode()).decode()
    req = urllib.request.Request(f'http://127.0.0.1:{port}{path}', headers={'Authorization': 'Basic ' + credential})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=8) as response:
        return response.read().decode()

def check_no_agents_stopped():
    assert agent.poll() is None, 'Stop must preserve the separate Herdr server/agent process tree'

try:
    assert 'test-session' in run('list').stdout
    assert json.loads(run('list', '--json').stdout)[0]['name'] == 'default'
    assert 'test-session' in run('llist').stdout
    assert not records('ttyd') and not records('cloudflared')
    assert not (state / 'instance.lock').exists()
    assert run('list', '--bad', ok=False).returncode
    print('PASS: list/llist/JSON are read-only; invalid list flags rejected')

    started = run('test-session')
    assert '用户名：herdr' in started.stdout and '密码：' in started.stdout
    assert meta()['session'] == 'test-session'
    worker_pid = int((state / 'instance.lock').read_text())
    ttyd_pid = int((state / 'ttyd.pid').read_text())
    cf_pid = int((state / 'cloudflared.pid').read_text())
    assert os.getsid(worker_pid) == os.getpgid(worker_pid) == worker_pid
    # Simulate a launcher/SSH process-group cleanup after the startup command exits.
    try:
        os.killpg(started.launcher_pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    assert all(live(p) for p in (worker_pid, ttyd_pid, cf_pid))
    assert not [r for r in records('herdr') if r['args'][:2] != ['session', 'list']]
    assert records('cloudflared')[-1]['home'] == os.environ['HOME']
    assert run('another-session', ok=False).returncode
    assert int((state / 'instance.lock').read_text()) == worker_pid
    assert len(records('ttyd')) == len(records('cloudflared')) == 1
    assert 'test-session' in run('status').stdout
    os.kill(worker_pid, signal.SIGHUP)
    time.sleep(0.2)
    assert all(live(p) for p in (worker_pid, ttyd_pid, cf_pid))
    assert records('ttyd')[-1]['hup_ignored']
    print('PASS: named session, one tunnel, credentials, nohup and SIGHUP survival; no eager client')

    for _ in range(3):
        child = int(request('/__test_connect'))
        wait_for(lambda: any(r['pid'] == child for r in records('herdr')))
        rec = next(r for r in records('herdr') if r['pid'] == child)
        assert rec['args'] == ['--session', 'test-session']
        assert rec['hup_ignored'] and not rec['inherited']
        assert request('/__test_disconnect') == 'closed'
        wait_for(lambda: not live(child))
    assert not records('leaked-client')
    check_no_agents_stopped()
    print('PASS: three blocked clients exit on disconnect via SIGTERM; session env does not override target')

    active_client = int(request('/__test_connect'))
    wait_for(lambda: live(active_client))
    run('stop')
    wait_for(lambda: not any(live(p) for p in (worker_pid, ttyd_pid, cf_pid, active_client)))
    check_no_agents_stopped()
    assert run('status', ok=False).returncode
    assert not list(state.glob('start-result.*'))
    print('PASS: stop cleans up the tunnel and attached client; preserves server/agents')

    run('default')
    child = int(request('/__test_connect'))
    wait_for(lambda: any(r['pid'] == child for r in records('herdr')))
    assert next(r for r in records('herdr') if r['pid'] == child)['args'] == []
    old_pid = int((state / 'instance.lock').read_text())
    run('restart')
    assert meta()['session'] == 'default'
    assert int((state / 'instance.lock').read_text()) != old_pid
    run('restart', 'test-session')
    assert meta()['session'] == 'test-session'
    check_no_agents_stopped()
    print('PASS: default session and restart with/without a new name')

    # Simulate an unexpected CF exit: no restart, no accumulation.
    old_count = len(records('cloudflared'))
    old_worker = int((state / 'instance.lock').read_text())
    old_ttyd = int((state / 'ttyd.pid').read_text())
    os.kill(int((state / 'cloudflared.pid').read_text()), signal.SIGTERM)
    wait_for(lambda: not live(old_worker) and not live(old_ttyd))
    assert len(records('cloudflared')) == old_count
    assert run('status', ok=False).returncode
    print('PASS: unexpected child exit cleans up once, without automatic restart')

    # SIGKILL skips supervisor traps: recover tracked orphans by PID + birth.
    run('test-session')
    old_worker = int((state / 'instance.lock').read_text())
    orphan_ttyd = int((state / 'ttyd.pid').read_text())
    orphan_cf = int((state / 'cloudflared.pid').read_text())
    os.kill(old_worker, signal.SIGKILL)
    wait_for(lambda: not live(old_worker))
    run('stop')
    wait_for(lambda: not live(orphan_ttyd) and not live(orphan_cf))
    check_no_agents_stopped()
    print('PASS: stop after supervisor crash cleans up only recorded orphan children')

    # A reused/unrelated PID must not be killed just because it appears in state.
    (state / 'cloudflared.pid').write_text(str(agent.pid) + '\n')
    (state / 'cloudflared.birth').write_text('not-the-same-process\n')
    run('stop')
    check_no_agents_stopped()
    print('PASS: stale PID record does not kill an unrelated process')

    (state / 'ttyd.pid').write_text(str(agent.pid) + '\n')
    assert run('stop', ok=False).returncode
    assert run('test-session', ok=False).returncode
    check_no_agents_stopped()
    (state / 'ttyd.pid').unlink()
    print('PASS: unverified legacy orphan is reported, retained and never blindly killed')

    (root / 'fail-cloudflared').touch()
    assert run('test-session', ok=False).returncode
    wait_for(lambda: not (state / 'instance.lock').exists())
    assert not live(records('ttyd')[-1]['pid'])
    (root / 'fail-cloudflared').unlink()
    print('PASS: failed startup cleans up ttyd and releases locks')

    first = subprocess.Popen(['bash', str(SCRIPT), 'test-session'], env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    second = subprocess.Popen(['bash', str(SCRIPT), 'other'], env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    first_output = first.communicate(timeout=30)
    second_output = second.communicate(timeout=30)
    assert sorted([first.returncode, second.returncode]) == [0, 1], (first.returncode, second.returncode, first_output, second_output)
    run('stop')
    check_no_agents_stopped()
    print('PASS: concurrent starts create exactly one tunnel')

    legacy = root / 'herdr-web-quick-tunnel.sh'
    legacy.write_text("#!/usr/bin/env bash\ntrap 'exit 0' TERM\nwhile true; do sleep 0.1; done\n")
    proc = subprocess.Popen(['bash', str(legacy), '__worker', 'test-session', str(root), 'unused'],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    (state / 'instance.lock').write_text(str(proc.pid) + '\n')
    try:
        assert run('another', ok=False).returncode
        assert proc.poll() is None
        run('stop')
        proc.wait(timeout=5)
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=5)
    print('PASS: recognizes and stops a legacy supervisor; refuses a duplicate')

    class OtherService(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
        def log_message(self, *_):
            pass
    server = http.server.HTTPServer(('127.0.0.1', port), OtherService)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    cf_count = len(records('cloudflared'))
    try:
        assert run('test-session', ok=False).returncode
        assert len(records('cloudflared')) == cf_count
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    assert not list(state.glob('start-result.*'))
    print('PASS: an occupied HTTP port is refused without starting a public connector')

    env['HERDR_WEB_TEST_IMAGE_UPLOAD'] = '1'
    pinned_html = data / 'ttyd-2922cb89f518bae4d0fcf4d757a7419638fc71fc.index.html'
    pinned_html.unlink(missing_ok=True)
    run('start', 'default')
    argv = records('ttyd')[-1]['args']
    assert '--image-upload' in argv and '-I' not in argv
    assert not pinned_html.exists(), 'An image-capable binary must use its embedded frontend'
    run('stop')
    print('PASS: detects image support, enables uploads and keeps the matching embedded frontend')
finally:
    try:
        run('stop')
    finally:
        agent.terminate()
        agent.wait(timeout=5)
    print('Fixture logs:', root)

#!/usr/bin/env python3
"""Exercise the PowerShell lifecycle on macOS/Linux with local-only doubles.

Requires pwsh. Windows-specific ACL, detached console and CIM are not emulated
as proven Windows behavior: native Windows still needs a real-machine smoke test.
"""
import base64
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

SCRIPT = Path(__file__).resolve().parents[1] / 'tun.ps1'
FIXTURE = Path(__file__).with_name('herdr-web-fixture.py')
PWSH = os.environ.get('HERDR_TEST_PWSH') or shutil.which('pwsh')
assert PWSH, 'Set HERDR_TEST_PWSH to a PowerShell 7 executable'
root = Path(tempfile.mkdtemp(prefix='herdr-web-powershell.'))
# Spaces and single quotes catch argument and path quoting regressions.
state, binaries, data = (root / name for name in ("state with 'quotes", 'bin with spaces', 'data with spaces'))
for directory in (state, binaries, data):
    directory.mkdir()
trace = root / 'trace.jsonl'
trace.touch()
for name in ('herdr.exe', 'ttyd.exe', 'cloudflared.exe'):
    binary = binaries / name
    binary.write_text('#!' + sys.executable + '\n' + FIXTURE.read_text().split('\n', 1)[1])
    binary.chmod(0o755)
(data / 'ttyd-2922cb89f518bae4d0fcf4d757a7419638fc71fc.index.html').write_text('registerOscHandler(52')
wrapper = root / "tun fixture's wrapper.ps1"
wrapper.write_text(". '" + str(SCRIPT).replace("'", "''") + "'\n" + r'''
Initialize-Context
$script:Self = $PSCommandPath
function Assert-WindowsRuntime {}
# CIM is Windows-only. Use the fixture's recorded parent IDs for this test.
function Get-WebClients($TtydRecord) {
    if (-not (Find-RecordedProcess $TtydRecord)) { return }
    foreach ($line in (Get-Content -LiteralPath $env:HERDR_WEB_TEST_TRACE)) {
        $rec = $line | ConvertFrom-Json
        if ($rec.event -eq 'herdr' -and $rec.parent -eq $TtydRecord.Id) {
            $proc = Get-Process -Id $rec.pid -ErrorAction SilentlyContinue
            if ($proc) { Get-Identity $proc }
        }
    }
}
try { Invoke-Tun -CommandArgs @($args) }
catch { [Console]::Error.WriteLine($_.Exception.Message + "`n" + $_.ScriptStackTrace); exit 1 }
''')
with socket.socket() as sock:
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
env = dict(os.environ, HERDR_WEB_STATE=str(state), HERDR_WEB_BIN=str(binaries), HERDR_WEB_DATA=str(data),
           HERDR_WEB_PORT=str(port), HERDR_WEB_TEST_TRACE=str(trace), HERDR_WEB_TEST_DIR=str(root),
           http_proxy='http://127.0.0.1:1', no_proxy='', NO_PROXY='',
           HERDR_SESSION='inherited-wrong-session', HERDR_SOCKET_PATH='/wrong/socket', HERDR_ENV='1')
agent = subprocess.Popen([sys.executable, str(FIXTURE), 'agent'], env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)

def run(*args, ok=True):
    proc = subprocess.run([PWSH, '-NoProfile', '-File', str(wrapper), *args], env=env, text=True,
                          capture_output=True, timeout=60)
    if ok and proc.returncode:
        raise AssertionError(f'{args}: {proc.stdout}\n{proc.stderr}')
    return proc

def meta():
    return json.loads((state / 'instance.json').read_text())

def identity(role):
    return json.loads((state / (role + '.json')).read_text())

def records(event):
    return [r for line in trace.read_text().splitlines() if (r := json.loads(line))['event'] == event]

def live(process_id):
    proc = subprocess.run(['ps', '-p', str(process_id), '-o', 'stat='], text=True, capture_output=True)
    return proc.returncode == 0 and bool(proc.stdout.strip()) and 'Z' not in proc.stdout

def wait_for(predicate, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.1)
    raise AssertionError('Timed out')

def request(path):
    info = meta()
    credential = base64.b64encode((info['Username'] + ':' + info['Password']).encode()).decode()
    req = urllib.request.Request(f'http://127.0.0.1:{port}{path}', headers={'Authorization': 'Basic ' + credential})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=8) as response:
        return response.read().decode()

try:
    assert 'test-session' in run('list').stdout
    assert json.loads(run('list', '--json').stdout)[0]['name'] == 'default'
    assert not list(state.iterdir())
    assert run('status', ok=False).returncode
    assert run('list', '--bad', ok=False).returncode
    print('PASS: list/JSON/status are read-only; status fails when stopped', flush=True)

    started = run('test-session')
    assert '用户名：herdr' in started.stdout and '密码：' in started.stdout
    assert meta()['Session'] == 'test-session'
    worker_id = identity('worker')['Id']
    ttyd_id = identity('ttyd')['Id']
    cf_id = identity('cloudflared')['Id']
    assert not [r for r in records('herdr') if r['args'][:2] != ['session', 'list']]
    assert run('another', ok=False).returncode
    assert identity('worker')['Id'] == worker_id
    assert len(records('ttyd')) == len(records('cloudflared')) == 1
    assert 'test-session' in run('status').stdout
    print('PASS: background startup survives parent exit, credentials, paths with quotes/spaces, one tunnel', flush=True)

    for _ in range(3):
        child = int(request('/__test_connect'))
        wait_for(lambda: any(r['pid'] == child for r in records('herdr')))
        rec = next(r for r in records('herdr') if r['pid'] == child)
        assert rec['args'] == ['--session', 'test-session'] and not rec['inherited']
        assert request('/__test_disconnect') == 'closed'
        wait_for(lambda: not live(child))
    active_client = int(request('/__test_connect'))
    wait_for(lambda: live(active_client))
    run('stop')
    wait_for(lambda: not any(live(p) for p in (worker_id, ttyd_id, cf_id, active_client)))
    assert agent.poll() is None
    assert run('status', ok=False).returncode
    print('PASS: disconnect and stop clean clients; separate Herdr server/agent survives', flush=True)

    run('default')
    child = int(request('/__test_connect'))
    wait_for(lambda: any(r['pid'] == child for r in records('herdr')))
    assert next(r for r in records('herdr') if r['pid'] == child)['args'] == []
    run('restart')
    assert meta()['Session'] == 'default'
    old_worker = identity('worker')['Id']
    assert run('restart', 'bad/name', ok=False).returncode
    assert identity('worker')['Id'] == old_worker
    run('restart', 'test-session')
    assert meta()['Session'] == 'test-session'
    print('PASS: default/named sessions, restart preserves name, invalid restart leaves tunnel running', flush=True)

    old_count = len(records('cloudflared'))
    old_worker, old_ttyd = identity('worker')['Id'], identity('ttyd')['Id']
    os.kill(identity('cloudflared')['Id'], signal.SIGTERM)
    wait_for(lambda: not live(old_worker) and not live(old_ttyd))
    assert len(records('cloudflared')) == old_count
    assert not list(state.glob('result-*.json'))
    print('PASS: crash cleans up once without restarting or stale result files', flush=True)

    run('test-session')
    old_worker, old_ttyd, old_cf = (identity(role)['Id'] for role in ('worker', 'ttyd', 'cloudflared'))
    os.kill(old_worker, signal.SIGKILL)
    wait_for(lambda: not live(old_worker))
    run('stop')
    wait_for(lambda: not live(old_ttyd) and not live(old_cf))
    assert agent.poll() is None
    (state / 'cloudflared.json').write_text(json.dumps({'Id': agent.pid, 'Started': 'wrong-birth'}))
    run('stop')
    assert agent.poll() is None
    print('PASS: crashed worker recovery; PID reuse never kills an unrelated process', flush=True)

    (root / 'fail-cloudflared').touch()
    assert run('test-session', ok=False).returncode
    (root / 'fail-cloudflared').unlink()
    assert not live(records('ttyd')[-1]['pid'])
    assert not (state / 'worker.json').exists()
    print('PASS: failed startup releases ttyd, state and runtime lock', flush=True)

    first = subprocess.Popen([PWSH, '-NoProfile', '-File', str(wrapper), 'one'], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    second = subprocess.Popen([PWSH, '-NoProfile', '-File', str(wrapper), 'two'], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    first_output, second_output = first.communicate(timeout=60), second.communicate(timeout=60)
    assert sorted([first.returncode, second.returncode]) == [0, 1], (first_output, second_output)
    run('stop')
    with socket.socket() as occupied:
        occupied.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        occupied.bind(('127.0.0.1', port)); occupied.listen()
        cf_count = len(records('cloudflared'))
        assert run('one', ok=False).returncode
        assert len(records('cloudflared')) == cf_count
    assert agent.poll() is None
    print('PASS: concurrent starts create one tunnel; occupied non-HTTP port refuses publication', flush=True)

    env['HERDR_WEB_TEST_IMAGE_UPLOAD'] = '1'
    pinned_html = data / 'ttyd-2922cb89f518bae4d0fcf4d757a7419638fc71fc.index.html'
    pinned_html.unlink(missing_ok=True)
    run('start', 'default')
    argv = records('ttyd')[-1]['args']
    assert '--image-upload' in argv and '-I' not in argv
    assert not pinned_html.exists()
    run('stop')
    print('PASS: image-capable binary uses its embedded frontend and enables image paste', flush=True)
finally:
    try:
        run('stop')
    finally:
        agent.terminate(); agent.wait(timeout=5)
    print('PowerShell fixture logs:', root, flush=True)

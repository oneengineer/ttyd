"""Small stdlib WebSocket client for real ttyd protocol tests."""
import base64
import json
import os
import socket
import ssl
import struct
import time


class WS:
    def __init__(self, port, path='/ws', authorization=None, host='127.0.0.1', secure=False):
        self.sock = socket.create_connection((host, port), timeout=8)
        if secure:
            self.sock = ssl.create_default_context().wrap_socket(self.sock, server_hostname=host)
        self.pending = b''
        key = base64.b64encode(os.urandom(16)).decode()
        request = (f'GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\n'
                   'Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Version: 13\r\n'
                   f'Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Protocol: tty\r\n')
        if authorization:
            request += f'Authorization: {authorization}\r\n'
        self.sock.sendall((request + '\r\n').encode())
        response = b''
        while b'\r\n\r\n' not in response:
            part = self.sock.recv(8192)
            if not part:
                break
            response += part
        header, _, self.pending = response.partition(b'\r\n\r\n')
        self.open = header.startswith(b'HTTP/1.1 101 ')
        self.output = b''

    def exact(self, length):
        while len(self.pending) < length:
            data = self.sock.recv(65536)
            if not data:
                raise EOFError('WebSocket disconnected')
            self.pending += data
        result, self.pending = self.pending[:length], self.pending[length:]
        return result

    def send(self, payload, opcode=2, final=True):
        length = len(payload)
        header = bytes([(128 if final else 0) | opcode])
        if length < 126:
            header += bytes([128 | length])
        elif length < 65536:
            header += b'\xfe' + struct.pack('!H', length)
        else:
            header += b'\xff' + struct.pack('!Q', length)
        mask = os.urandom(4)
        self.sock.sendall(header + mask + bytes(c ^ mask[i % 4] for i, c in enumerate(payload)))

    def receive(self, seconds=4):
        self.sock.settimeout(seconds)
        try:
            first, second = self.exact(2)
        except EOFError:
            return 'close', None
        length = second & 127
        if length == 126:
            length = struct.unpack('!H', self.exact(2))[0]
        elif length == 127:
            length = struct.unpack('!Q', self.exact(8))[0]
        data = self.exact(length)
        opcode = first & 15
        if opcode == 9:
            self.send(data, 10)
            return self.receive(seconds)
        if opcode == 8:
            return ('close', struct.unpack('!H', data[:2])[0] if len(data) > 1 else None)
        if data[:1] == b'0':
            self.output += data[1:]
        return data[:1].decode(), data[1:]

    def expect(self, command, seconds=4):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            kind, data = self.receive(max(.01, deadline - time.monotonic()))
            if kind == command:
                return data
            assert kind != 'close', (kind, data)
        raise AssertionError(f'No command {command!r}')

    def login(self, token):
        assert self.open
        self.send(json.dumps(dict(AuthToken=token, columns=100, rows=28)).encode())
        return json.loads(self.expect('2'))

    def close(self):
        try:
            self.send(struct.pack('!H', 1000), 8)
        except OSError:
            pass
        self.sock.close()


def wait_for(predicate, seconds=5):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.03)
    raise AssertionError('Timed out waiting for expected state')

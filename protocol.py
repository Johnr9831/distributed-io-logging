"""Shared TCP framing and protocol constants. Owner: both teammates (freeze after integration)."""

import json
import socket
import struct

RECORD_ID = struct.Struct('!Q')  # 8-byte unsigned record sequence number.
ACK = struct.Struct('!Q')        # 8-byte acknowledgment after successful fsync.
HEADER_LEN = struct.Struct('!I')
MAX_HEADER_BYTES = 16384
DEFAULT_RECORD_SIZE = 1024


def recv_exact(sock: socket.socket, n: int) -> bytes:
    """Read precisely n bytes or raise EOFError; TCP does not preserve messages."""
    received = bytearray()
    while len(received) < n:
        part = sock.recv(n - len(received))
        if not part:
            raise EOFError(f'Connection closed after {len(received)} of {n} bytes')
        received.extend(part)
    return bytes(received)


def send_header(sock: socket.socket, header: dict) -> None:
    raw = json.dumps(header, separators=(',', ':')).encode('utf-8')
    if len(raw) > MAX_HEADER_BYTES:
        raise ValueError('Header is too long')
    sock.sendall(HEADER_LEN.pack(len(raw)) + raw)


def recv_header(sock: socket.socket) -> dict:
    n = HEADER_LEN.unpack(recv_exact(sock, HEADER_LEN.size))[0]
    if n < 1 or n > MAX_HEADER_BYTES:
        raise ValueError(f'Invalid header length: {n}')
    obj = json.loads(recv_exact(sock, n).decode('utf-8'))
    if not isinstance(obj, dict):
        raise ValueError('Header must be a JSON object')
    return obj

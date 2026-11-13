"""Raspberry Pi TCP log server: per-record fsync vs timed/bounded batch fsync.

Owner: person 1.
Wire protocol: one length-prefixed JSON hello, one length-prefixed ready, then
fixed-length binary records. Each ACK is an 8-byte sequence number, sent ONLY
after os.fsync has returned successfully. One client/run at a time.
"""

import argparse
import json
import os
import re
import select
import socket
import time
from datetime import datetime, timezone
from pathlib import Path

from monitor import RunMonitor
from protocol import ACK, DEFAULT_RECORD_SIZE, RECORD_ID, recv_header, send_header

RUN_ID_RE = re.compile(r'^[a-zA-Z0-9_-]{1,80}$')


def validate_hello(h: dict) -> dict:
    if h.get('type') != 'hello':
        raise ValueError("Expected a hello header")
    run_id = h.get('run_id')
    if not isinstance(run_id, str) or not RUN_ID_RE.fullmatch(run_id):
        raise ValueError('run_id must be 1-80 letters, digits, hyphens or underscores')
    mode = h.get('mode')
    if mode not in ('individual', 'batch'):
        raise ValueError('mode must be individual or batch')
    record_size = h.get('record_size')
    batch_size = h.get('batch_size')
    batch_timeout_ms = h.get('batch_timeout_ms')
    seed = h.get('seed')
    if type(record_size) is not int or not RECORD_ID.size <= record_size <= 1024 * 1024:
        raise ValueError('record_size must be an integer between 8 and 1048576')
    if type(batch_size) is not int or not 1 <= batch_size <= 10000:
        raise ValueError('batch_size must be an integer between 1 and 10000')
    if type(batch_timeout_ms) not in (int, float) or not 1 <= batch_timeout_ms <= 60000:
        raise ValueError('batch_timeout_ms must be between 1 and 60000')
    if type(seed) is not int or not 0 <= seed < 2 ** 32:
        raise ValueError('seed must be an integer between 0 and 2^32 - 1')
    return {
        'run_id': run_id, 'mode': mode, 'record_size': record_size,
        'batch_size': batch_size, 'batch_timeout_ms': batch_timeout_ms,
        'seed': seed,
    }


def write_all(fd: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        count = os.write(fd, view)
        if count <= 0:
            raise OSError('Unable to write to log file')
        view = view[count:]


def handle_connection(conn: socket.socket, peer, data_dir: Path, disk_device=None) -> None:
    conn.settimeout(10.0)
    try:
        cfg = validate_hello(recv_header(conn))
    except (EOFError, ValueError, json.JSONDecodeError, socket.timeout) as exc:
        try:
            send_header(conn, {'type': 'error', 'message': str(exc)})
        except OSError:
            pass
        print(f'[invalid handshake from {peer}] {exc}', flush=True)
        return

    run_id = cfg['run_id']
    path = data_dir / (run_id + '.bin')
    summary_path = data_dir / (run_id + '.server.json')
    monitor_path = data_dir / (run_id + '.monitor.csv')
    if path.exists() or summary_path.exists():
        send_header(conn, {'type': 'error', 'message': f'run_id {run_id} already exists'})
        return

    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except OSError as exc:
        send_header(conn, {'type': 'error', 'message': str(exc)})
        return

    start = time.monotonic()
    started_utc = datetime.now(timezone.utc).isoformat()
    stats = {
        **cfg, 'peer': str(peer), 'started_utc': started_utc,
        'status': 'running', 'records_received': 0, 'records_acked': 0,
        'fsync_calls': 0, 'write_batches': 0, 'bytes_written': 0,
        'flush_on_size': 0, 'flush_on_timeout': 0, 'flush_on_eof': 0,
        'monitor_error': None, 'error': None, 'disk_device': disk_device,
    }
    monitor = RunMonitor(monitor_path, disk_device=disk_device)
    pending = []
    first_pending_at = None
    received_buffer = bytearray()
    expected_seq = 0
    record_size = cfg['record_size']
    batched = cfg['mode'] == 'batch'
    timeout_seconds = cfg['batch_timeout_ms'] / 1000
    clean_eof = False
    monitor_started = False

    def flush(reason: str) -> None:
        nonlocal pending, first_pending_at
        if not pending:
            return
        block = b''.join(pending)
        write_all(fd, block)
        stats['bytes_written'] += len(block)
        stats['write_batches'] += 1
        os.fsync(fd)
        stats['fsync_calls'] += 1
        # ACKs deliberately follow successful fsync (not merely buffered write).
        ack_block = b''.join(ACK.pack(RECORD_ID.unpack_from(item)[0]) for item in pending)
        conn.sendall(ack_block)
        stats['records_acked'] += len(pending)
        if reason in ('size', 'timeout', 'eof'):
            stats['flush_on_' + reason] += 1
        pending.clear()
        first_pending_at = None

    try:
        monitor.start()
        monitor_started = True
        send_header(conn, {'type': 'ready', 'run_id': run_id})
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        conn.setblocking(True)
        print(f'[START] {run_id} mode={cfg["mode"]} peer={peer}', flush=True)
        while True:
            if batched and pending:
                remaining = first_pending_at + timeout_seconds - time.monotonic()
                if remaining <= 0:
                    flush('timeout')
                    continue
                wait_time = remaining
            else:
                wait_time = None

            readable, _, _ = select.select([conn], [], [], wait_time)
            if not readable:
                flush('timeout')
                continue
            fragment = conn.recv(65536)
            if not fragment:
                if received_buffer:
                    raise ValueError(f'Truncated final record: {len(received_buffer)} bytes')
                flush('eof')
                clean_eof = True
                break
            received_buffer.extend(fragment)
            while len(received_buffer) >= record_size:
                record = bytes(received_buffer[:record_size])
                del received_buffer[:record_size]
                seq = RECORD_ID.unpack_from(record)[0]
                if seq != expected_seq:
                    raise ValueError(f'Unexpected sequence: got {seq}; expected {expected_seq}')
                expected_seq += 1
                stats['records_received'] += 1
                if not pending:
                    first_pending_at = time.monotonic()
                pending.append(record)
                if not batched:
                    flush('individual')
                elif len(pending) >= cfg['batch_size']:
                    flush('size')
                elif time.monotonic() - first_pending_at >= timeout_seconds:
                    flush('timeout')
        stats['status'] = 'completed' if clean_eof else 'incomplete'
    except (OSError, ValueError) as exc:
        stats['status'] = 'failed'
        stats['error'] = repr(exc)
        print(f'[ERROR] {run_id}: {exc}', flush=True)
    finally:
        if monitor_started:
            monitor.stop()
            stats['monitor_error'] = monitor.error
        os.close(fd)
        stats['ended_utc'] = datetime.now(timezone.utc).isoformat()
        stats['elapsed_s'] = round(time.monotonic() - start, 4)
        summary_path.write_text(json.dumps(stats, indent=2), encoding='utf-8')
        print(f'[END] {run_id}: {stats["status"]}, '
              f'received={stats["records_received"]}, acked={stats["records_acked"]}, '
              f'fsync={stats["fsync_calls"]}', flush=True)


def main():
    parser = argparse.ArgumentParser(description='TCP logging server on Raspberry Pi')
    parser.add_argument('--bind', default='0.0.0.0')
    parser.add_argument('--port', type=int, default=5000)
    parser.add_argument('--data-dir', default='data')
    parser.add_argument('--disk-device', default=None, help='Optional psutil disk name, e.g. mmcblk0')
    args = parser.parse_args()
    data_dir = Path(args.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((args.bind, args.port))
        listener.listen(5)
        print(f'Listening at {args.bind}:{args.port}; output: {data_dir.resolve()}', flush=True)
        try:
            while True:
                conn, peer = listener.accept()
                with conn:
                    handle_connection(conn, peer, data_dir, args.disk_device)
        except KeyboardInterrupt:
            print('\nServer stopped', flush=True)


if __name__ == '__main__':
    main()

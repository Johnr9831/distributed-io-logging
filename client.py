"""VM/Docker log generator and end-to-end ACK latency recorder. Owner: person 2."""

import argparse
import csv
import json
import random
import socket
import statistics
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from protocol import ACK, DEFAULT_RECORD_SIZE, RECORD_ID, recv_header, send_header


def percentile(values, p):
    if not values:
        return None
    ordered = sorted(values)
    idx = (len(ordered) - 1) * p / 100.0
    lower = int(idx)
    frac = idx - lower
    if frac == 0 or lower == len(ordered) - 1:
        return ordered[lower]
    return ordered[lower] * (1.0 - frac) + ordered[lower + 1] * frac


def experiment(args):
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    if args.record_size < RECORD_ID.size:
        raise ValueError('record-size must be >= 8 bytes')
    if args.seed < 0 or args.seed >= 2 ** 32:
        raise ValueError('seed out of range')
    if args.rate <= 0 or args.duration <= 0 or args.drain_timeout < 0:
        raise ValueError('rate, duration must be positive; drain-timeout >= 0')

    total_planned = int(args.rate * args.duration)
    if total_planned < 1:
        raise ValueError('This run schedules zero records')
    sent_ns = [None] * total_planned
    ack_ns = [None] * total_planned
    receive_errors = []
    stop_reader = threading.Event()
    send_error = None
    sent_count = 0
    bad_acks = 0
    duplicate_acks = 0
    rng = random.Random(args.seed)
    requested_utc = datetime.now(timezone.utc).isoformat()
    hello = {
        'type': 'hello', 'run_id': args.run_id, 'mode': args.mode,
        'record_size': args.record_size, 'batch_size': args.batch_size,
        'batch_timeout_ms': args.batch_timeout_ms, 'seed': args.seed,
    }

    with socket.create_connection((args.host, args.port), timeout=10) as sock:
        send_header(sock, hello)
        response = recv_header(sock)
        if response.get('type') != 'ready':
            raise RuntimeError(f'Server rejected run: {response}')
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.settimeout(2)

        def receive_acks():
            nonlocal bad_acks, duplicate_acks
            buffer = bytearray()
            try:
                while not stop_reader.is_set():
                    try:
                        fragment = sock.recv(8192)
                    except socket.timeout:
                        continue
                    if not fragment:
                        break
                    buffer.extend(fragment)
                    while len(buffer) >= ACK.size:
                        seq = ACK.unpack_from(buffer)[0]
                        del buffer[:ACK.size]
                        arrival = time.perf_counter_ns()
                        if seq >= total_planned or sent_ns[seq] is None:
                            bad_acks += 1
                        elif ack_ns[seq] is not None:
                            duplicate_acks += 1
                        else:
                            ack_ns[seq] = arrival
                if buffer:
                    receive_errors.append('Server sent a partial ACK')
            except OSError as exc:
                if not stop_reader.is_set():
                    receive_errors.append(repr(exc))

        receiver = threading.Thread(target=receive_acks, daemon=True)
        receiver.start()
        start_ns = time.perf_counter_ns()
        end_ns = start_ns + int(args.duration * 1_000_000_000)
        interval_ns = 1_000_000_000 / args.rate

        try:
            for seq in range(total_planned):
                due_ns = start_ns + int(seq * interval_ns)
                delay_ns = due_ns - time.perf_counter_ns()
                if delay_ns > 0:
                    time.sleep(delay_ns / 1_000_000_000)
                if time.perf_counter_ns() >= end_ns:
                    break
                data = RECORD_ID.pack(seq) + rng.randbytes(args.record_size - RECORD_ID.size)
                sent_ns[seq] = time.perf_counter_ns()
                sock.sendall(data)
                sent_count += 1
        except OSError as exc:
            send_error = repr(exc)
        finally:
            try:
                sock.shutdown(socket.SHUT_WR)
            except OSError:
                pass
            receiver.join(timeout=args.drain_timeout)
            if receiver.is_alive():
                stop_reader.set()
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                receiver.join(timeout=3)
                receive_errors.append(f'ACK drain timed out after {args.drain_timeout}s')

    latencies_ms = [
        (ack_ns[i] - sent_ns[i]) / 1_000_000
        for i in range(sent_count) if ack_ns[i] is not None
    ]
    acked_count = len(latencies_ms)
    acks_within_window = sum(
        1 for i in range(sent_count)
        if ack_ns[i] is not None and ack_ns[i] <= end_ns
    )
    last_ack_ns = max((x for x in ack_ns[:sent_count] if x is not None), default=None)
    total_elapsed_s = ((last_ack_ns - start_ns) / 1e9) if last_ack_ns is not None else None
    ok = (sent_count > 0 and acked_count == sent_count and
          not send_error and not receive_errors and bad_acks == 0 and duplicate_acks == 0)
    summary = {
        'run_id': args.run_id, 'started_utc': requested_utc,
        'host': args.host, 'port': args.port,
        'mode': args.mode, 'rate_target': args.rate,
        'duration_s': args.duration, 'record_size': args.record_size,
        'batch_size': args.batch_size, 'batch_timeout_ms': args.batch_timeout_ms,
        'seed': args.seed, 'records_planned': total_planned,
        'records_sent': sent_count, 'records_acked': acked_count,
        'records_unacked': sent_count - acked_count,
        'acks_within_window': acks_within_window,
        'throughput_window_logs_s': round(acks_within_window / args.duration, 4),
        'throughput_total_logs_s': round(acked_count / total_elapsed_s, 4) if total_elapsed_s and total_elapsed_s > 0 else None,
        'send_rate_actual_logs_s': round(sent_count / args.duration, 4),
        'ack_success_pct': round(100 * acked_count / sent_count, 4) if sent_count else 0,
        'latency_mean_ms': round(statistics.mean(latencies_ms), 4) if latencies_ms else None,
        'latency_p50_ms': round(percentile(latencies_ms, 50), 4) if latencies_ms else None,
        'latency_p95_ms': round(percentile(latencies_ms, 95), 4) if latencies_ms else None,
        'latency_max_ms': round(max(latencies_ms), 4) if latencies_ms else None,
        'bad_acks': bad_acks, 'duplicate_acks': duplicate_acks,
        'send_error': send_error, 'receive_errors': receive_errors,
        'status': 'completed' if ok else 'incomplete',
    }
    with (output_dir / (args.run_id + '.latency.csv')).open('w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['run_id', 'seq', 'sent_elapsed_ms', 'ack_elapsed_ms', 'latency_ms', 'status'])
        for i in range(sent_count):
            ack = ack_ns[i]
            writer.writerow([
                args.run_id, i,
                round((sent_ns[i] - start_ns) / 1e6, 5),
                round((ack - start_ns) / 1e6, 5) if ack is not None else '',
                round((ack - sent_ns[i]) / 1e6, 5) if ack is not None else '',
                'acked' if ack is not None else 'unacked',
            ])
    (output_dir / (args.run_id + '.client.json')).write_text(
        json.dumps(summary, indent=2), encoding='utf-8'
    )
    print(json.dumps(summary, indent=2))
    return 0 if ok else 2


def main():
    parser = argparse.ArgumentParser(description='Scheduled TCP log generator with ACK metrics')
    parser.add_argument('--host', required=True, help='Raspberry Pi IP')
    parser.add_argument('--port', type=int, default=5000)
    parser.add_argument('--mode', choices=['individual', 'batch'], required=True)
    parser.add_argument('--rate', type=int, default=100, help='Scheduled logs per second')
    parser.add_argument('--duration', type=float, default=60, help='Active workload duration in seconds')
    parser.add_argument('--run-id', required=True, help='Unique filename-safe run ID')
    parser.add_argument('--record-size', type=int, default=DEFAULT_RECORD_SIZE)
    parser.add_argument('--batch-size', type=int, default=50)
    parser.add_argument('--batch-timeout-ms', type=float, default=50)
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--drain-timeout', type=float, default=60,
                        help='Additional time to collect ACKs after sending ends')
    parser.add_argument('--output-dir', default='results')
    args = parser.parse_args()
    raise SystemExit(experiment(args))


if __name__ == '__main__':
    main()

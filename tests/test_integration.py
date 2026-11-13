import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class IntegrationTests(unittest.TestCase):
    def test_both_modes_end_to_end(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            port = free_port()
            server = subprocess.Popen(
                [sys.executable, '-u', str(ROOT / 'server.py'),
                 '--bind', '127.0.0.1', '--port', str(port),
                 '--data-dir', str(base / 'data')],
                stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
            )
            try:
                for _ in range(60):
                    if server.poll() is not None:
                        self.fail('Server terminated before test')
                    try:
                        with socket.create_connection(('127.0.0.1', port), timeout=0.1):
                            break
                    except OSError:
                        time.sleep(0.05)
                else:
                    self.fail('Server did not accept connections')

                for mode in ('individual', 'batch'):
                    run_id = f'test_{mode}'
                    completed = subprocess.run(
                        [sys.executable, str(ROOT / 'client.py'),
                         '--host', '127.0.0.1', '--port', str(port),
                         '--mode', mode, '--rate', '30', '--duration', '1.0',
                         '--batch-size', '5', '--batch-timeout-ms', '40',
                         '--run-id', run_id, '--output-dir', str(base / 'results')],
                        capture_output=True, text=True, timeout=30,
                    )
                    self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
                    client = json.loads((base / 'results' / (run_id + '.client.json')).read_text())
                    server_stats = json.loads((base / 'data' / (run_id + '.server.json')).read_text())
                    self.assertEqual(client['records_sent'], client['records_acked'])
                    self.assertEqual(server_stats['records_received'], client['records_sent'])
                    self.assertEqual(server_stats['status'], 'completed')
                    self.assertLessEqual(server_stats['fsync_calls'], server_stats['records_acked'])
                    checked = subprocess.run(
                        [sys.executable, str(ROOT / 'verify.py'),
                         '--run-id', run_id, '--data-dir', str(base / 'data')],
                        capture_output=True, text=True, timeout=15,
                    )
                    self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
                    if mode == 'individual':
                        self.assertEqual(server_stats['fsync_calls'], client['records_acked'])
                    else:
                        self.assertLess(server_stats['fsync_calls'], client['records_acked'])
            finally:
                server.terminate()
                try:
                    server.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=5)


if __name__ == '__main__':
    unittest.main()

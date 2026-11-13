"""One-second server-side system/process metrics sampler. Owner: person 1."""

import csv
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

try:
    import psutil
except ImportError:
    psutil = None


class RunMonitor:
    FIELDS = [
        'timestamp_utc', 'elapsed_s', 'system_cpu_pct', 'server_cpu_pct',
        'server_rss_mb', 'system_memory_pct', 'disk_written_bytes_per_s',
        'disk_write_ops_per_s',
    ]

    def __init__(self, output_path: Path, interval: float = 1.0, disk_device=None):
        self.output_path = Path(output_path)
        self.interval = interval
        self.disk_device = disk_device
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.error = None

    def start(self):
        if psutil is None:
            raise RuntimeError('Missing psutil: pip install -r requirements-server.txt')
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread.ident is not None:
            self.thread.join(timeout=3)

    def _disk_io(self):
        if self.disk_device is None:
            return psutil.disk_io_counters()
        counters = psutil.disk_io_counters(perdisk=True)
        if self.disk_device not in counters:
            raise ValueError(f'Unknown disk device {self.disk_device!r}; available: {sorted(counters)}')
        return counters[self.disk_device]

    def _run(self):
        try:
            import time
            process = psutil.Process(os.getpid())
            process.cpu_percent(interval=None)
            psutil.cpu_percent(interval=None)
            prev_io = self._disk_io()
            prev_time = time.monotonic()
            start = prev_time
            with self.output_path.open('w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=self.FIELDS)
                writer.writeheader()
                while True:
                    stopped = self.stop_event.wait(self.interval)
                    now = time.monotonic()
                    delta = max(now - prev_time, 1e-9)
                    current_io = self._disk_io()
                    if current_io and prev_io:
                        write_rate = max(0, current_io.write_bytes - prev_io.write_bytes) / delta
                        write_ops_rate = max(0, current_io.write_count - prev_io.write_count) / delta
                    else:
                        write_rate = write_ops_rate = 0
                    writer.writerow({
                        'timestamp_utc': datetime.now(timezone.utc).isoformat(),
                        'elapsed_s': round(now - start, 3),
                        'system_cpu_pct': psutil.cpu_percent(interval=None),
                        'server_cpu_pct': process.cpu_percent(interval=None),
                        'server_rss_mb': round(process.memory_info().rss / (1024 ** 2), 3),
                        'system_memory_pct': psutil.virtual_memory().percent,
                        'disk_written_bytes_per_s': round(write_rate, 2),
                        'disk_write_ops_per_s': round(write_ops_rate, 3),
                    })
                    f.flush()
                    if stopped:
                        break
                    prev_io = current_io
                    prev_time = now
        except Exception as exc:
            self.error = repr(exc)

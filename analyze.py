"""Aggregate run-level metrics and optionally plot them. Owner: person 2.

stdlib required; matplotlib optional (requirements-analysis.txt).
Copy Pi-side *.server.json and *.monitor.csv into --server-data-dir first
if CPU/disk comparisons are needed. Exclude incomplete runs from aggregates.
"""

import argparse
import csv
import json
import statistics
from collections import defaultdict
from pathlib import Path


METRICS = [
    'throughput_window_logs_s', 'latency_mean_ms', 'latency_p50_ms',
    'latency_p95_ms', 'ack_success_pct', 'server_cpu_pct',
    'system_cpu_pct', 'server_rss_mb', 'disk_written_mb_per_s',
    'fsync_per_record',
]


def write_csv(path, rows, fields):
    with Path(path).open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)


def enrich_from_pi(run, server_data_dir):
    if server_data_dir is None:
        return
    run_id = run['run_id']
    stats_file = server_data_dir / (run_id + '.server.json')
    if stats_file.exists():
        stats = json.loads(stats_file.read_text(encoding='utf-8'))
        run['server_status'] = stats.get('status', 'unknown')
        run['server_fsync_calls'] = stats.get('fsync_calls')
        run['server_records_received'] = stats.get('records_received')
        run['server_records_acked'] = stats.get('records_acked')
        acks = stats.get('records_acked', 0)
        run['fsync_per_record'] = (stats['fsync_calls'] / acks) if acks else None
    monitor_file = server_data_dir / (run_id + '.monitor.csv')
    if monitor_file.exists():
        with monitor_file.open(newline='', encoding='utf-8') as f:
            rows = list(csv.DictReader(f))
        for col in ('server_cpu_pct', 'system_cpu_pct', 'server_rss_mb'):
            vals = [float(r[col]) for r in rows if r.get(col) not in ('', None)]
            run[col] = statistics.mean(vals) if vals else None
        disk_values = [float(r['disk_written_bytes_per_s']) / (1024 ** 2)
                       for r in rows if r.get('disk_written_bytes_per_s') not in ('', None)]
        run['disk_written_mb_per_s'] = statistics.mean(disk_values) if disk_values else None


def main():
    parser = argparse.ArgumentParser(description='Summarize throughput and ACK latency')
    parser.add_argument('--results-dir', default='results')
    parser.add_argument('--server-data-dir', default=None,
                        help='Folder containing copied *.server.json and *.monitor.csv from Pi')
    parser.add_argument('--no-plots', action='store_true')
    args = parser.parse_args()
    folder = Path(args.results_dir)
    pi_folder = Path(args.server_data_dir) if args.server_data_dir else None
    all_runs = []
    for path in sorted(folder.glob('*.client.json')):
        try:
            run = json.loads(path.read_text(encoding='utf-8'))
            enrich_from_pi(run, pi_folder)
            all_runs.append(run)
        except (ValueError, OSError) as exc:
            print(f'Skipping {path}: {exc}')
    if not all_runs:
        raise SystemExit(f'No *.client.json found in {folder}')

    run_fields = ['run_id', 'mode', 'rate_target', 'duration_s', 'status',
                  'records_sent', 'records_acked', 'records_unacked',
                  'server_status', 'server_fsync_calls', 'server_records_received'] + METRICS
    write_csv(folder / 'run_table.csv', all_runs, run_fields)
    by_condition = defaultdict(list)
    for run in all_runs:
        if run.get('status') == 'completed' and run.get('server_status', 'completed') == 'completed':
            by_condition[(run['mode'], run['rate_target'])].append(run)

    aggregated = []
    for (mode, rate), group in sorted(by_condition.items(), key=lambda item: (item[0][1], item[0][0])):
        row = {'mode': mode, 'rate_target': rate, 'completed_runs': len(group)}
        for metric in METRICS:
            vals = [float(r[metric]) for r in group if r.get(metric) is not None]
            row[metric + '_mean'] = round(statistics.mean(vals), 4) if vals else ''
            row[metric + '_stdev'] = round(statistics.stdev(vals), 4) if len(vals) > 1 else ''
        aggregated.append(row)
    aggr_fields = ['mode', 'rate_target', 'completed_runs'] + [
        suffix for metric in METRICS for suffix in (metric + '_mean', metric + '_stdev')
    ]
    write_csv(folder / 'aggregate.csv', aggregated, aggr_fields)
    print(f'Loaded {len(all_runs)} runs, '
          f'{sum(r["status"] == "completed" for r in all_runs)} client-complete')
    for row in aggregated:
        print(f'{row["mode"]:10s} rate={row["rate_target"]:>5} '
              f'n={row["completed_runs"]} '
              f'throughput={row["throughput_window_logs_s_mean"]} logs/s '
              f'p95={row["latency_p95_ms_mean"]} ms')

    if args.no_plots:
        return
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
    except ImportError:
        print('Plots skipped (install: pip install -r requirements-analysis.txt)')
        return

    figures = [
        ('throughput_window_logs_s_mean', 'ACK-confirmed logs/s (active window)', 'throughput.png'),
        ('latency_p95_ms_mean', 'Mean of per-run p95 ACK latency (ms)', 'latency_p95.png'),
        ('server_cpu_pct_mean', 'Server process CPU (%)', 'server_cpu.png'),
        ('disk_written_mb_per_s_mean', 'Disk writes (MiB/s)', 'disk_writes.png'),
    ]
    for metric, ylabel, filename in figures:
        if not any(r[metric] != '' for r in aggregated):
            continue
        fig, ax = plt.subplots(figsize=(8, 5))
        for mode in ('individual', 'batch'):
            relevant = [r for r in aggregated if r['mode'] == mode and r[metric] != '']
            if relevant:
                ax.plot([r['rate_target'] for r in relevant], [r[metric] for r in relevant],
                        marker='o', label=mode)
        ax.set_xlabel('Scheduled records per second')
        ax.set_ylabel(ylabel)
        ax.set_title('Individual vs batched fsync (measured)')
        ax.grid(alpha=0.3)
        ax.legend()
        fig.tight_layout()
        fig.savefig(folder / filename, dpi=160)
        plt.close(fig)
    print(f'Wrote: {folder / "aggregate.csv"} and available plots')


if __name__ == '__main__':
    main()

"""Run all workload x mode x repetition combinations. Owner: person 2.

By default 2 modes x 3 rates x 5 repetitions = 30 runs of 60 s each.
Alternates method order for each (rate, repetition) pairing.
"""

import argparse
import csv
import json
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description='Automate all client scenarios')
    parser.add_argument('--host', required=True)
    parser.add_argument('--port', type=int, default=5000)
    parser.add_argument('--rates', default='100,300,600')
    parser.add_argument('--duration', type=float, default=60.0)
    parser.add_argument('--repetitions', type=int, default=5)
    parser.add_argument('--pause', type=float, default=5.0, help='Seconds between runs')
    parser.add_argument('--record-size', type=int, default=1024)
    parser.add_argument('--batch-size', type=int, default=50)
    parser.add_argument('--batch-timeout-ms', type=float, default=50)
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--drain-timeout', type=float, default=60)
    parser.add_argument('--output-dir', default='results')
    parser.add_argument('--pilot', action='store_true',
                        help='2 modes x 1 rate, 5 s each (initial connectivity test)')
    args = parser.parse_args()

    if args.pause < 0 or args.repetitions < 1:
        parser.error('pause >= 0 and repetitions >= 1 are required')
    rates = [int(part.strip()) for part in args.rates.split(',')]
    if not rates or any(x <= 0 for x in rates):
        parser.error('Rates must be a comma-separated list of positive integers')
    if args.pilot:
        rates = [rates[0]]
        args.duration = min(args.duration, 5.0)
        args.repetitions = 1
        args.pause = 0

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    plan = []
    for rep in range(1, args.repetitions + 1):
        for r_index, rate in enumerate(rates):
            modes = ['individual', 'batch']
            if (rep + r_index) % 2 == 0:
                modes.reverse()
            for mode in modes:
                plan.append((rep, rate, mode))

    print(f'Planned runs: {len(plan)}; total active workload '
          f'{len(plan) * args.duration / 60:.1f} minutes; '
          'additional drain/pause time is not included.', flush=True)
    manifest = out / 'manifest.csv'
    with manifest.open('a', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=[
            'run_id', 'repetition', 'rate', 'mode', 'start_utc', 'end_utc',
            'exit_code', 'client_summary',
        ])
        if handle.tell() == 0:
            writer.writeheader()
        for index, (rep, rate, mode) in enumerate(plan, start=1):
            prefix = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')
            run_id = f'{prefix}_{mode}_r{rate}_rep{rep:02d}_{uuid.uuid4().hex[:6]}'
            cmd = [
                sys.executable, str(Path(__file__).with_name('client.py')),
                '--host', args.host, '--port', str(args.port),
                '--mode', mode, '--rate', str(rate),
                '--duration', str(args.duration), '--run-id', run_id,
                '--record-size', str(args.record_size),
                '--batch-size', str(args.batch_size),
                '--batch-timeout-ms', str(args.batch_timeout_ms),
                '--seed', str(args.seed),
                '--drain-timeout', str(args.drain_timeout),
                '--output-dir', str(out),
            ]
            print(f'[{index}/{len(plan)}] mode={mode} rate={rate} rep={rep} id={run_id}',
                  flush=True)
            started = datetime.now(timezone.utc).isoformat()
            log_path = out / (run_id + '.stdout.txt')
            with log_path.open('w', encoding='utf-8') as log:
                result = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, check=False)
            ended = datetime.now(timezone.utc).isoformat()
            writer.writerow({
                'run_id': run_id, 'repetition': rep, 'rate': rate, 'mode': mode,
                'start_utc': started, 'end_utc': ended,
                'exit_code': result.returncode,
                'client_summary': (run_id + '.client.json'),
            })
            handle.flush()
            print(f'   exit={result.returncode}, log={log_path}', flush=True)
            if index < len(plan) and args.pause:
                time.sleep(args.pause)
    print(f'Finished. Manifest: {manifest}', flush=True)
    print(f'Aggregate data: python analyze.py --results-dir {out}', flush=True)


if __name__ == '__main__':
    main()

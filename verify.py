"""Independently verify every stored sequence ID and deterministic payload. Owner: person 1."""

import argparse
import json
import random
from pathlib import Path

from protocol import RECORD_ID


def verify(data_file: Path, record_size: int, seed: int):
    rng = random.Random(seed)
    count = 0
    mismatch = None
    with Path(data_file).open('rb') as file:
        while True:
            record = file.read(record_size)
            if not record:
                break
            if len(record) != record_size:
                mismatch = f'Truncated record #{count}: {len(record)} bytes'
                break
            seq = RECORD_ID.unpack_from(record)[0]
            expected = RECORD_ID.pack(count) + rng.randbytes(record_size - RECORD_ID.size)
            if seq != count or record != expected:
                mismatch = f'Corrupted or out-of-order record #{count}, stored ID={seq}'
                break
            count += 1
    return {'records_verified': count, 'record_size': record_size,
            'byte_count_verified': count * record_size,
            'status': 'OK' if mismatch is None else 'FAILED', 'error': mismatch}


def main():
    parser = argparse.ArgumentParser(description='Verify Raspberry Pi storage file')
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--data-dir', default='data')
    args = parser.parse_args()
    folder = Path(args.data_dir)
    summary = json.loads((folder / (args.run_id + '.server.json')).read_text(encoding='utf-8'))
    outcome = verify(folder / (args.run_id + '.bin'), summary['record_size'], summary['seed'])
    outcome['expected_received'] = summary['records_received']
    outcome['expected_acked'] = summary['records_acked']
    if outcome['records_verified'] != summary['records_received']:
        outcome['status'] = 'FAILED'
        outcome['error'] = 'Stored record count does not match server received count'
    if summary['records_acked'] > outcome['records_verified']:
        outcome['status'] = 'FAILED'
        outcome['error'] = 'Server acknowledges more records than are stored'
    print(json.dumps(outcome, indent=2))
    raise SystemExit(0 if outcome['status'] == 'OK' else 2)


if __name__ == '__main__':
    main()

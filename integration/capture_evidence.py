#!/usr/bin/env python3
"""Sample controller state and actual OVS counters during a Mininet scenario.

Run in VM terminal 3 as root, BEFORE sourcing a scenario in Mininet.
This records observations; it never labels a test as passed automatically.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import time
from urllib.request import urlopen


def command(args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=4)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or ' '.join(args))
    return result.stdout


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--seconds', type=float, default=55)
    parser.add_argument('--interval', type=float, default=1)
    args = parser.parse_args()
    if args.seconds <= 0 or args.interval <= 0:
        parser.error('seconds and interval must be positive')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents accidentally replacing a previous trial.
    with args.output.open('x', encoding='utf-8') as stream:
        end = time.monotonic() + args.seconds
        while time.monotonic() < end:
            start = time.monotonic()
            row = dict(ts=time.time(), utc=datetime.now(timezone.utc).isoformat())
            try:
                with urlopen('http://127.0.0.1:8080/status', timeout=2) as response:
                    row['controller'] = json.load(response)
                row['flows'] = command(['ovs-ofctl', '-O', 'OpenFlow13', 'dump-flows', 's1'])
                row['queues'] = command(['ovs-ofctl', '-O', 'OpenFlow13', 'queue-stats', 's1'])
            except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
                row['error'] = str(error)
            stream.write(json.dumps(row) + '\n'); stream.flush()
            time.sleep(max(0, min(args.interval - (time.monotonic()-start), end-time.monotonic())))
    print('Saved', args.output, '- inspect errors/state/flow/counters before claiming a pass.')


if __name__ == '__main__': main()

import argparse
import time

import psutil

from stats import collect_stats
from udp_client import load_config, create_socket, send_message


def classify(stats, cfg):
    """Return 'critical' if CPU or memory crosses the agreed thresholds."""
    if stats["cpu"] > cfg["cpu_critical"] or stats["mem"] > cfg["mem_critical"]:
        return "critical"
    return "normal"


def main():
    try:
        cfg = load_config()
    except (OSError, ValueError) as e:
        raise SystemExit(f"Cannot read config.json: {e}")

    parser = argparse.ArgumentParser()
    parser.add_argument("--id", required=True, help="node id, e.g. agent1")
    parser.add_argument("--server", default=cfg["server_ip"])
    parser.add_argument("--interval", type=float, default=cfg["interval_sec"])
    parser.add_argument("--critical-after", type=float, default=None,
                        help="demo: start faking critical CPU after N seconds")
    parser.add_argument("--critical-for", type=float, default=10,
                        help="demo: how many seconds the fake critical state lasts")
    args = parser.parse_args()

    sock = create_socket()
    seq = 0
    start = time.time()

    psutil.cpu_percent(interval=None)   # warm up
    time.sleep(1)

    print(f"{args.id} started, server={args.server}, interval={args.interval}s. "
          f"Press Ctrl+C to stop.")

    try:
        while True:
            try:
                stats = collect_stats()
            except Exception as e:
                print(f"could not collect stats: {e}")
                time.sleep(args.interval)
                continue

            # demo mode: fake a critical CPU for a while
            elapsed = time.time() - start
            if (args.critical_after is not None
                    and args.critical_after <= elapsed < args.critical_after + args.critical_for):
                stats["cpu"] = 95.0

            status = classify(stats, cfg)
            port = cfg["critical_port"] if status == "critical" else cfg["normal_port"]

            report = {
                "node_id": args.id,
                "seq": seq,
                "ts": time.time(),
                "status": status,
                **stats,
            }

            try:
                send_message(sock, report, args.server, port)
                print(f"sent seq={seq} status={status} port={port} "
                      f"cpu={stats['cpu']} mem={stats['mem']}")
            except OSError as e:
                print(f"send failed for seq={seq}: {e}")

            seq += 1
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print(f"\n{args.id} stopped.")
    finally:
        sock.close()


if __name__ == "__main__":
    main()

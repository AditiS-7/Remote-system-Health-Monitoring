import argparse
import time

import psutil

from stats import collect_stats
from udp_client import load_config, create_socket, send_message


def main():
    cfg = load_config()

    parser = argparse.ArgumentParser()
    parser.add_argument("--id", required=True, help="node id, e.g. agent1")
    parser.add_argument("--server", default=cfg["server_ip"])
    parser.add_argument("--interval", type=float, default=cfg["interval_sec"])
    args = parser.parse_args()

    sock = create_socket()
    seq = 0

    psutil.cpu_percent(interval=None)   # warm up, first call always returns 0.0
    time.sleep(1)

    print(f"{args.id} started, sending to {args.server}:{cfg['normal_port']} "
          f"every {args.interval}s. Press Ctrl+C to stop.")

    try:
        while True:
            report = {
                "node_id": args.id,
                "seq": seq,
                "ts": time.time(),
                "status": "normal",     # Step 4 will decide normal/critical
                **collect_stats(),      # adds cpu, mem, procs, net_sent, net_recv
            }
            send_message(sock, report, args.server, cfg["normal_port"])
            print(f"sent seq={seq} cpu={report['cpu']} mem={report['mem']}")
            seq += 1
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print(f"\n{args.id} stopped.")
    finally:
        sock.close()


if __name__ == "__main__":
    main()

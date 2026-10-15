#!/usr/bin/env python3
"""
Fake monitoring agent for testing the server before Person 1's agents are ready.

Examples:
    python3 fake_agent.py --id agent1                       # normal load
    python3 fake_agent.py --id agent2 --mode critical       # CPU/mem spike -> CRITICAL
    python3 fake_agent.py --id agent3 --die-after 8         # stops -> heartbeat lost
    python3 fake_agent.py --id agent1 --bad                 # sends malformed packets
"""
import argparse
import json
import random
import socket
import time


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", required=True)
    ap.add_argument("--server", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5005)
    ap.add_argument("--interval", type=float, default=2.0)
    ap.add_argument("--mode", choices=["normal", "warning", "critical", "spike"], default="normal",
                    help="spike = normal, then critical for a while, then normal again")
    ap.add_argument("--die-after", type=int, help="stop sending after N reports")
    ap.add_argument("--bad", action="store_true", help="send malformed packets")
    args = ap.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    seq, sent, recv = 0, 0, 0
    while args.die_after is None or seq < args.die_after:
        seq += 1
        mode = args.mode
        if mode == "spike":
            mode = "critical" if 4 <= seq <= 8 else "normal"
        cpu = {"normal": random.uniform(5, 40), "warning": random.uniform(76, 85),
               "critical": random.uniform(91, 99)}[mode]
        mem = {"normal": random.uniform(30, 60), "warning": random.uniform(76, 85),
               "critical": random.uniform(91, 97)}[mode]
        sent += random.randint(500, 5000)
        recv += random.randint(500, 5000)
        pkt = {
            "node_id": args.id, "seq": seq, "timestamp": time.time(),
            "type": "critical" if mode == "critical" else "normal",
            "cpu_percent": round(cpu, 1), "memory_percent": round(mem, 1),
            "process_count": random.randint(150, 300),
            "net": {"bytes_sent": sent, "bytes_recv": recv,
                    "packets_sent": sent // 100, "packets_recv": recv // 100},
        }
        payload = b"not json at all" if args.bad else json.dumps(pkt).encode()
        sock.sendto(payload, (args.server, args.port))
        print(f"{args.id} -> seq={seq} cpu={pkt['cpu_percent']} mem={pkt['memory_percent']} [{mode}]")
        time.sleep(args.interval)


if __name__ == "__main__":
    main()

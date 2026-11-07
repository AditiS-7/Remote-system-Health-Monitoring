"""
agent.py - UDP health monitoring agent (D2 version)

Every interval the agent collects CPU / memory / process / network stats,
marks the report normal or critical, and sends it to the monitoring server:
    normal   -> UDP port 5000
    critical -> UDP port 5001

New in D2:
    session_id   random ID chosen at start, so the server can tell a restarted
                 agent (seq back to 0) from lost packets
    report_id    "<node_id>-<session_id>-<seq>", unique per report
    --log-file   CSV sender log (one row per report, with send timestamp)
    --duration   stop automatically after N seconds
    --scenario   scripted run such as normal:10,critical:20,normal:10
"""
import argparse
import csv
import os
import time
import uuid

import psutil

from stats import collect_stats
from udp_client import load_config, create_socket, send_message

CSV_FIELDS = ["report_id", "session_id", "node_id", "seq", "ts_send",
              "status", "port", "cpu", "mem", "send_ok", "error"]


def classify(stats, cfg):
    """Return 'critical' if CPU or memory crosses the agreed thresholds."""
    if stats["cpu"] > cfg["cpu_critical"] or stats["mem"] > cfg["mem_critical"]:
        return "critical"
    return "normal"


def parse_scenario(text):
    """'normal:10,critical:20,normal:10' -> [('normal', 10.0), ('critical', 20.0), ('normal', 10.0)]"""
    phases = []
    for part in text.split(","):
        try:
            state, secs = part.strip().split(":")
            state = state.strip().lower()
            secs = float(secs)
            if state not in ("normal", "critical") or secs <= 0:
                raise ValueError
        except ValueError:
            raise SystemExit(
                f"Bad --scenario part '{part}'. Use state:seconds, where state is "
                f"normal or critical, e.g. normal:10,critical:20,normal:10")
        phases.append((state, secs))
    return phases


def scenario_state(phases, elapsed):
    """Which state the scenario wants at this time (None once it is over)."""
    t = 0.0
    for state, secs in phases:
        t += secs
        if elapsed < t:
            return state
    return None


def open_log(path):
    """Open the CSV sender log (append mode). Returns (file, writer) or (None, None)."""
    if not path:
        return None, None
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    is_new = not os.path.exists(path) or os.path.getsize(path) == 0
    f = open(path, "a", newline="")
    writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
    if is_new:
        writer.writeheader()
        f.flush()
    return f, writer


def main():
    try:
        cfg = load_config()
    except (OSError, ValueError) as e:
        raise SystemExit(f"Cannot read config.json: {e}")

    parser = argparse.ArgumentParser()
    parser.add_argument("--id", required=True, help="node id, e.g. agent1")
    parser.add_argument("--server", default=cfg["server_ip"])
    parser.add_argument("--interval", type=float, default=cfg["interval_sec"])
    parser.add_argument("--session-id", default=None,
                        help="override the random session id (normally leave unset)")
    parser.add_argument("--log-file", default=None,
                        help="write a CSV sender log to this path")
    parser.add_argument("--duration", type=float, default=None,
                        help="stop automatically after N seconds")
    parser.add_argument("--scenario", default=None,
                        help="scripted run, e.g. normal:10,critical:20,normal:10 "
                             "(stops by itself at the end; overrides --critical-after)")
    parser.add_argument("--critical-after", type=float, default=None,
                        help="demo: start faking critical CPU after N seconds")
    parser.add_argument("--critical-for", type=float, default=10,
                        help="demo: how many seconds the fake critical state lasts")
    args = parser.parse_args()

    scenario = parse_scenario(args.scenario) if args.scenario else None
    if scenario and args.duration is None:
        args.duration = sum(secs for _, secs in scenario)

    session_id = args.session_id or uuid.uuid4().hex[:8]
    sock = create_socket()
    log_file, log_writer = open_log(args.log_file)
    seq = 0
    sent_ok = 0
    failed = 0
    critical_sent = 0

    psutil.cpu_percent(interval=None)   # warm up
    time.sleep(1)
    start = time.time()                 # --duration / --scenario timing starts here

    print(f"{args.id} started, session={session_id}, server={args.server}, "
          f"interval={args.interval}s. Press Ctrl+C to stop.", flush=True)

    try:
        while True:
            if args.duration is not None and time.time() - start >= args.duration:
                print(f"{args.id}: duration {args.duration}s reached.", flush=True)
                break

            try:
                stats = collect_stats()
            except Exception as e:
                print(f"could not collect stats: {e}", flush=True)
                time.sleep(args.interval)
                continue

            # test modes: fake a critical CPU on demand
            elapsed = time.time() - start
            if scenario:
                if scenario_state(scenario, elapsed) == "critical":
                    stats["cpu"] = 95.0
            elif (args.critical_after is not None
                    and args.critical_after <= elapsed < args.critical_after + args.critical_for):
                stats["cpu"] = 95.0

            status = classify(stats, cfg)
            port = cfg["critical_port"] if status == "critical" else cfg["normal_port"]

            report = {
                "node_id": args.id,
                "session_id": session_id,
                "report_id": f"{args.id}-{session_id}-{seq}",
                "seq": seq,
                "status": status,
                **stats,
            }

            report["ts"] = time.time()          # stamped right before sending
            send_ok = True
            error = ""
            try:
                send_message(sock, report, args.server, port)
                sent_ok += 1
                if status == "critical":
                    critical_sent += 1
                print(f"sent seq={seq} status={status} port={port} "
                      f"cpu={stats['cpu']} mem={stats['mem']}", flush=True)
            except OSError as e:
                send_ok = False
                error = str(e)
                failed += 1
                print(f"send failed for seq={seq}: {e}", flush=True)

            if log_writer:
                log_writer.writerow({
                    "report_id": report["report_id"],
                    "session_id": session_id,
                    "node_id": args.id,
                    "seq": seq,
                    "ts_send": f"{report['ts']:.6f}",
                    "status": status,
                    "port": port,
                    "cpu": stats["cpu"],
                    "mem": stats["mem"],
                    "send_ok": int(send_ok),
                    "error": error,
                })
                log_file.flush()

            seq += 1
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print(f"\n{args.id} stopped.", flush=True)
    finally:
        sock.close()
        if log_file:
            log_file.close()
        print(f"{args.id} session {session_id} summary: sent_ok={sent_ok} "
              f"failed={failed} critical_sent={critical_sent}", flush=True)


if __name__ == "__main__":
    main()

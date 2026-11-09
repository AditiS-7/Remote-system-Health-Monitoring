"""
parse_capture.py - turn a tcpdump capture of the agent reports into a receive log.

The capture is taken on the server host's interface (server-eth0), so each
packet's timestamp is when the report arrived at the server, after any
queueing in the switch.

Usage:
    python3 experiments/parse_capture.py capture.txt recv_log.csv
    python3 experiments/parse_capture.py capture.txt recv_log.csv --agent-logs results/agent_logs

Capture command (inside Mininet):
    server tcpdump -i server-eth0 -n -tt -l -A -s 0 "udp and (dst port 5000 or dst port 5001)" > capture.txt &

With --agent-logs, the receive times are matched to the agents' sender logs by
report_id, and a table of sent / received / loss / delay per node and status
is printed.
"""
import argparse
import csv
import glob
import os
import re
from collections import defaultdict

HEADER = re.compile(
    r"^(\d+\.\d+) IP (\d+\.\d+\.\d+\.\d+)\.(\d+) > (\d+\.\d+\.\d+\.\d+)\.(\d+): UDP")
REPORT_ID = re.compile(r'"report_id":\s*"([^"]+)"')


def parse_capture(path):
    """Return one dict per captured report: report_id, ts_recv, src_ip, dst_port."""
    blocks, cur = [], None
    with open(path, errors="replace") as f:
        for line in f:
            h = HEADER.match(line)
            if h:
                cur = {"ts": h.group(1), "src": h.group(2),
                       "dport": h.group(5), "text": []}
                blocks.append(cur)
            elif cur is not None:
                cur["text"].append(line)

    rows = []
    for b in blocks:
        m = REPORT_ID.search(" ".join(b["text"]))
        if m:
            rows.append({"report_id": m.group(1), "ts_recv": b["ts"],
                         "src_ip": b["src"], "dst_port": b["dport"]})
    return rows


def write_csv(rows, path):
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["report_id", "ts_recv", "src_ip", "dst_port"])
        w.writeheader()
        w.writerows(rows)


def summarize(agent_dir, rows):
    """Match receive times to sender logs and print loss and delay per node/status."""
    recv = {r["report_id"]: float(r["ts_recv"]) for r in rows}
    stats = defaultdict(lambda: {"sent": 0, "got": 0, "delays": []})

    for path in sorted(glob.glob(os.path.join(agent_dir, "agent*.csv"))):
        with open(path, newline="") as f:
            for r in csv.DictReader(f):
                if r["send_ok"] != "1":
                    continue
                s = stats[(r["node_id"], r["status"])]
                s["sent"] += 1
                t = recv.get(r["report_id"])
                if t is not None:
                    s["got"] += 1
                    s["delays"].append((t - float(r["ts_send"])) * 1000.0)

    print(f"{'node':8} {'status':9} {'sent':>5} {'recv':>5} {'loss%':>6} "
          f"{'avg_ms':>9} {'max_ms':>9}")
    for (node, status), s in sorted(stats.items()):
        d = s["delays"]
        loss = 100.0 * (s["sent"] - s["got"]) / s["sent"] if s["sent"] else 0.0
        avg = sum(d) / len(d) if d else float("nan")
        mx = max(d) if d else float("nan")
        print(f"{node:8} {status:9} {s['sent']:>5} {s['got']:>5} {loss:>6.1f} "
              f"{avg:>9.2f} {mx:>9.2f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("capture", help="tcpdump text capture")
    ap.add_argument("out_csv", help="receive log to write")
    ap.add_argument("--agent-logs", default=None,
                    help="folder with agentN.csv sender logs, for the summary table")
    args = ap.parse_args()

    rows = parse_capture(args.capture)
    write_csv(rows, args.out_csv)
    print(f"{len(rows)} reports found in capture -> {args.out_csv}")
    if args.agent_logs:
        summarize(args.agent_logs, rows)


if __name__ == "__main__":
    main()

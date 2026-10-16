#!/usr/bin/env python3
"""
D1 - Central Monitoring Server (Person 2)

Receives periodic JSON health reports from UDP agents, tracks the latest
status of every node, raises alerts for CPU > 85% or RAM > 90%,
detects missing heartbeats after 6 seconds, and displays a live terminal dashboard.
"""

Usage:
    python3 monitor_server.py                    # uses config.json
    python3 monitor_server.py -c config.json
    python3 monitor_server.py --no-dashboard     # log-only mode (useful in tests)

import argparse
import json
import logging
import os
import signal
import socket
import sys
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from urllib import request as urlrequest

DEFAULT_CONFIG = {
    "server": {
        "host": "0.0.0.0",
        "ports": [5000, 5001],
        "workers": 8,
        "buffer_size": 4096
    },
    "thresholds": {
        "cpu_warning": 75.0,
        "cpu_critical": 85.0,
        "memory_warning": 75.0,
        "memory_critical": 90.0
    },
    "heartbeat": {
        "timeout_sec": 6.0,
        "check_interval_sec": 1.0
    },
    "alerts": {
        "cooldown_sec": 10.0,
        "log_file": "alerts.log"
    },
    "dashboard": {
        "enabled": True,
        "refresh_sec": 1.0,
        "alert_history": 6
    },
    "controller": {
        "enabled": False,
        "url": "http://127.0.0.1:8080/alert",
        "timeout_sec": 2.0
    }
}

# Node states
OK, WARNING, CRITICAL, DOWN = "OK", "WARNING", "CRITICAL", "DOWN"
COLORS = {OK: "\033[92m", WARNING: "\033[93m", CRITICAL: "\033[91m", DOWN: "\033[95m"}
RESET, BOLD = "\033[0m", "\033[1m"


def deep_merge(base, override):
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def load_config(path):
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if path and os.path.exists(path):
        with open(path) as f:
            deep_merge(cfg, json.load(f))
    elif path:
        print(f"[warn] config {path} not found, using defaults", file=sys.stderr)
    return cfg


class Node:
    def __init__(self, node_id, addr):
        self.node_id = node_id
        self.addr = addr
        self.status = OK
        self.agent_type = "normal"
        self.first_seen = time.time()
        self.last_seen = time.time()
        self.cpu = 0.0
        self.mem = 0.0
        self.procs = None
        self.bytes_sent = None
        self.bytes_recv = None
        self.tx_rate = 0.0
        self.rx_rate = 0.0
        self.reports = 0
        self.last_seq = None
        self.lost = 0
        self.last_critical_alert = 0.0


class Monitor:
    def __init__(self, cfg):
        self.cfg = cfg
        self.nodes = {}
        self.lock = threading.Lock()
        self.alerts = deque(maxlen=cfg["dashboard"]["alert_history"])
        self.stats = {"received": 0, "malformed": 0}
        self.stop_event = threading.Event()
        self.pool = ThreadPoolExecutor(max_workers=cfg["server"]["workers"])
        self.sockets = []
        self.log = logging.getLogger("monitor")

    def parse(self, data):
        """Decode datagram and adapt agent JSON schema."""
        try:
            pkt = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
            
        if not isinstance(pkt, dict) or not pkt.get("node_id"):
            return None

        # Field mapping: support both 'cpu'/'mem' and fallback 'cpu_percent'/'memory_percent'
        cpu_val = pkt.get("cpu", pkt.get("cpu_percent"))
        mem_val = pkt.get("mem", pkt.get("memory_percent"))

        if cpu_val is None or mem_val is None:
            return None

        try:
            parsed = {
                "node_id": str(pkt["node_id"]),
                "cpu": float(cpu_val),
                "mem": float(mem_val),
                "status": str(pkt.get("status", "normal")).lower(),
                "procs": pkt.get("procs", pkt.get("process_count")),
                "seq": pkt.get("seq"),
            }

            # Handle network fields (net_sent/net_recv or net dict)
            if "net_sent" in pkt or "net_recv" in pkt:
                parsed["net_sent"] = pkt.get("net_sent")
                parsed["net_recv"] = pkt.get("net_recv")
            elif isinstance(pkt.get("net"), dict):
                parsed["net_sent"] = pkt["net"].get("bytes_sent")
                parsed["net_recv"] = pkt["net"].get("bytes_recv")
            else:
                parsed["net_sent"] = None
                parsed["net_recv"] = None

            return parsed
        except (TypeError, ValueError):
            return None

    def classify(self, cpu, mem, agent_type):
        """Strict '>' check against thresholds."""
        th = self.cfg["thresholds"]
        reasons, level = [], OK

        if cpu > th["cpu_critical"]:
            level = CRITICAL
            reasons.append(f"CPU {cpu:.1f}% > {th['cpu_critical']}%")
        elif cpu > th["cpu_warning"]:
            level = WARNING
            reasons.append(f"CPU {cpu:.1f}% > {th['cpu_warning']}%")

        if mem > th["memory_critical"]:
            level = CRITICAL
            reasons.append(f"MEM {mem:.1f}% > {th['memory_critical']}%")
        elif mem > th["memory_warning"]:
            if level == OK:
                level = WARNING
            reasons.append(f"MEM {mem:.1f}% > {th['memory_warning']}%")

        if agent_type in ("critical", "high") and level != CRITICAL:
            level = CRITICAL
            reasons.append(f"agent status reported {agent_type}")

        return level, reasons

    def handle_datagram(self, data, addr):
        pkt = self.parse(data)
        if pkt is None:
            with self.lock:
                self.stats["malformed"] += 1
            self.log.warning("malformed packet from %s:%s dropped", *addr)
            return

        now = time.time()
        to_fire = []
        with self.lock:
            self.stats["received"] += 1
            node = self.nodes.get(pkt["node_id"])
            if node is None:
                node = self.nodes[pkt["node_id"]] = Node(pkt["node_id"], addr)
                self.log.info("new node registered: %s (%s)", node.node_id, addr[0])

            # Packet loss calculation
            seq = pkt.get("seq")
            if isinstance(seq, int):
                if node.last_seq is not None and seq > node.last_seq + 1:
                    node.lost += seq - node.last_seq - 1
                node.last_seq = seq

            # Throughput calculation
            sent, recv = pkt.get("net_sent"), pkt.get("net_recv")
            dt = now - node.last_seen
            if (node.reports > 0 and dt > 0 and isinstance(sent, (int, float))
                    and node.bytes_sent is not None):
                node.tx_rate = max(0.0, (sent - node.bytes_sent) / dt)
                node.rx_rate = max(0.0, (recv - node.bytes_recv) / dt)
            node.bytes_sent, node.bytes_recv = sent, recv

            node.addr = addr
            node.last_seen = now
            node.cpu, node.mem = pkt["cpu"], pkt["mem"]
            node.procs = pkt.get("procs")
            node.agent_type = pkt.get("status", "normal")
            node.reports += 1

            old = node.status
            level, reasons = self.classify(node.cpu, node.mem, node.agent_type)
            node.status = level

            cooldown = self.cfg["alerts"]["cooldown_sec"]
            if level == CRITICAL and (old != CRITICAL or now - node.last_critical_alert >= cooldown):
                node.last_critical_alert = now
                to_fire.append(self.make_alert(node, "CRITICAL", reasons))
            elif level == WARNING and old in (OK, DOWN):
                to_fire.append(self.make_alert(node, "WARNING", reasons))
            elif level == OK and old in (CRITICAL, DOWN, WARNING):
                to_fire.append(self.make_alert(node, "RECOVERED", ["back to normal"]))

        for alert in to_fire:
            self.fire_alert(alert)

    @staticmethod
    def make_alert(node, level, reasons):
        return {
            "time": datetime.now().isoformat(timespec="seconds"),
            "node_id": node.node_id,
            "src_ip": node.addr[0],
            "level": level,
            "reasons": reasons,
            "cpu_percent": node.cpu,
            "memory_percent": node.mem,
        }

    def fire_alert(self, alert):
        msg = f"[{alert['level']}] {alert['node_id']} ({alert['src_ip']}): {'; '.join(alert['reasons'])}"
        self.log.warning(msg) if alert["level"] != "RECOVERED" else self.log.info(msg)
        with self.lock:
            self.alerts.append(alert)
        if alert["level"] in ("CRITICAL", "RECOVERED"):
            self.pool.submit(self.notify_controller, alert)

    def notify_controller(self, alert):
        c = self.cfg["controller"]
        if not c["enabled"]:
            return
        try:
            req = urlrequest.Request(
                c["url"], data=json.dumps(alert).encode(),
                headers={"Content-Type": "application/json"}, method="POST")
            urlrequest.urlopen(req, timeout=c["timeout_sec"]).close()
            self.log.info("alert for %s forwarded to controller", alert["node_id"])
        except Exception as e:
            self.log.error("controller notify failed: %s", e)

    def heartbeat_loop(self):
        """Mark DOWN if no report received within timeout (6.0s)."""
        hb = self.cfg["heartbeat"]
        while not self.stop_event.wait(hb["check_interval_sec"]):
            now = time.time()
            to_fire = []
            with self.lock:
                for node in self.nodes.values():
                    if node.status != DOWN and now - node.last_seen > hb["timeout_sec"]:
                        node.status = DOWN
                        to_fire.append(self.make_alert(
                            node, "NODE_DOWN",
                            [f"no heartbeat for {now - node.last_seen:.1f}s"]))
            for alert in to_fire:
                self.fire_alert(alert)

    @staticmethod
    def fmt_rate(bps):
        for unit in ("B/s", "KB/s", "MB/s"):
            if bps < 1024:
                return f"{bps:6.1f} {unit}"
            bps /= 1024
        return f"{bps:6.1f} GB/s"

    def render(self):
        now = time.time()
        srv = self.cfg["server"]
        ports_str = ",".join(map(str, srv.get("ports", [srv.get("port", 5000)])))
        lines = [
            f"{BOLD}D1 CENTRAL MONITORING SERVER{RESET}   "
            f"udp/[{ports_str}]   {datetime.now():%Y-%m-%d %H:%M:%S}",
            f"packets: {self.stats['received']} ok, {self.stats['malformed']} malformed"
            f"   thresholds: CPU {self.cfg['thresholds']['cpu_warning']}/"
            f"{self.cfg['thresholds']['cpu_critical']}%  MEM "
            f"{self.cfg['thresholds']['memory_warning']}/"
            f"{self.cfg['thresholds']['memory_critical']}%",
            "",
            f"{BOLD}{'NODE':<10}{'IP':<15}{'STATUS':<10}{'CPU%':>6}{'MEM%':>7}"
            f"{'PROCS':>7}{'TX':>13}{'RX':>13}{'RPTS':>6}{'LOST':>6}{'AGE':>6}{RESET}",
        ]
        for nid in sorted(self.nodes):
            n = self.nodes[nid]
            age = now - n.last_seen
            col = COLORS[n.status]
            lines.append(
                f"{n.node_id:<10}{n.addr[0]:<15}{col}{n.status:<10}{RESET}"
                f"{n.cpu:>6.1f}{n.mem:>7.1f}{str(n.procs if n.procs is not None else '-'):>7}"
                f"{self.fmt_rate(n.tx_rate):>13}{self.fmt_rate(n.rx_rate):>13}"
                f"{n.reports:>6}{n.lost:>6}{age:>5.0f}s")
        if not self.nodes:
            lines.append("  (waiting for agents...)")
        lines += ["", f"{BOLD}RECENT ALERTS{RESET}"]
        for a in list(self.alerts)[::-1]:
            lines.append(f"  {a['time'][11:]}  {a['level']:<9} {a['node_id']:<8} "
                         f"{'; '.join(a['reasons'])}")
        return "\n".join(lines)

    def dashboard_loop(self):
        refresh = self.cfg["dashboard"]["refresh_sec"]
        while not self.stop_event.wait(refresh):
            with self.lock:
                frame = self.render()
            sys.stdout.write("\033[2J\033[H" + frame + "\n")
            sys.stdout.flush()

    def listen_port(self, sock):
        while not self.stop_event.is_set():
            try:
                data, addr = sock.recvfrom(self.cfg["server"]["buffer_size"])
            except socket.timeout:
                continue
            except OSError:
                break
            self.pool.submit(self.handle_datagram, data, addr)

    def serve(self):
        srv = self.cfg["server"]
        ports = srv.get("ports") or [srv.get("port", 5000)]

        for port in ports:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((srv["host"], port))
            sock.settimeout(1.0)
            self.sockets.append(sock)
            self.log.info("listening on udp %s:%s", srv["host"], port)

            t = threading.Thread(target=self.listen_port, args=(sock,), daemon=True)
            t.start()

        threading.Thread(target=self.heartbeat_loop, daemon=True).start()
        if self.cfg["dashboard"]["enabled"]:
            threading.Thread(target=self.dashboard_loop, daemon=True).start()

        while not self.stop_event.is_set():
            time.sleep(0.5)

    def shutdown(self):
        self.stop_event.set()
        for sock in self.sockets:
            try:
                sock.close()
            except Exception:
                pass
        self.pool.shutdown(wait=False)


def main():
    ap = argparse.ArgumentParser(description="D1 central monitoring server")
    ap.add_argument("-c", "--config", default="config.json")
    ap.add_argument("--port", type=int, help="override main UDP port")
    ap.add_argument("--no-dashboard", action="store_true")
    args = ap.parse_args()

    cfg = load_config(args.config)
    if args.port:
        cfg["server"]["ports"] = [args.port]
    if args.no_dashboard:
        cfg["dashboard"]["enabled"] = False

    handlers = [logging.FileHandler(cfg["alerts"]["log_file"])]
    if not cfg["dashboard"]["enabled"]:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)-7s %(message)s")

    mon = Monitor(cfg)
    signal.signal(signal.SIGTERM, lambda *_: mon.shutdown())
    try:
        mon.serve()
    except KeyboardInterrupt:
        pass
    finally:
        mon.shutdown()
        print("\nserver stopped")


if __name__ == "__main__":
    main()

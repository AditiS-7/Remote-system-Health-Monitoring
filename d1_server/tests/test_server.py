#!/usr/bin/env python3
"""Server tests (Person 2). No Mininet, root or real agents needed.

Run from the repo root:
    python3 -m unittest discover -s d1_server/tests -v
"""
import csv
import json
import logging
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
import warnings
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import monitor_server as m  # noqa: E402

logging.disable(logging.CRITICAL)
warnings.simplefilter("ignore", ResourceWarning)
ADDR = ("10.0.0.1", 40000)


class Controller:
    """Stand-in for the Ryu alert API. fail_first=N answers the first N POSTs with HTTP 500."""

    def __init__(self, fail_first=0):
        self.received, self.fail_first, self.calls = [], fail_first, 0
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                outer.calls += 1
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                code = 500 if outer.calls <= outer.fail_first else 200
                if code == 200:
                    outer.received.append(body)
                self.send_response(code)
                self.end_headers()

            def log_message(self, *a):
                pass

        self.httpd = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}/alert"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def wait_for(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def read_csv(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


class ServerTestBase(unittest.TestCase):
    controller_url = None
    heartbeat_timeout = 60.0

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.rpt = os.path.join(self.tmp, "reports.csv")
        self.evt = os.path.join(self.tmp, "events.csv")
        cfg = m.load_config(None)
        cfg["dashboard"]["enabled"] = False
        cfg["server"]["ports"] = [0, 0]
        cfg["heartbeat"].update(timeout_sec=self.heartbeat_timeout, check_interval_sec=0.1)
        cfg["alerts"]["cooldown_sec"] = 10.0
        cfg["logging"].update(report_csv=self.rpt, event_csv=self.evt)
        cfg["controller"].update(enabled=bool(self.controller_url), timeout_sec=1.0,
                                 retries=3, retry_delay_sec=0.1)
        if self.controller_url:
            cfg["controller"]["url"] = self.controller_url
        self.mon = m.Monitor(cfg)

    def tearDown(self):
        self.mon.shutdown()

    def send(self, seq, sid="S1", cpu=10.0, mem=20.0, node="agent1", **extra):
        d = {"node_id": node, "cpu": cpu, "mem": mem, "seq": seq, "ts": time.time() - 0.01}
        if sid:
            d["session_id"] = sid
            d["report_id"] = f"{node}-{sid}-{seq}"
        d.update(extra)
        self.raw(json.dumps(d).encode())

    def raw(self, data, addr=ADDR, port=5000):
        self.mon.handle_datagram(data, addr, time.time(), port)

    @property
    def node(self):
        return self.mon.nodes["agent1"]


class TestReportValidation(ServerTestBase):
    def test_malformed_reports_are_dropped_and_counted(self):
        bad = [b"not json", b"\xff\xfe\x00", b"[1, 2, 3]", b'{"cpu": 1, "mem": 2}',
               b'{"node_id": "a", "mem": 2}', b'{"node_id": "a", "cpu": "x", "mem": 2}', b""]
        for b in bad:
            self.raw(b)
        self.assertEqual(self.mon.stats["malformed"], len(bad))
        self.assertEqual(self.mon.nodes, {})
        flags = [r["flag"] for r in read_csv(self.rpt)]
        self.assertEqual(flags, ["malformed"] * len(bad))

    def test_server_keeps_working_after_malformed(self):
        self.raw(b"garbage")
        self.send(0)
        self.assertEqual(self.node.reports, 1)

    def test_both_cpu_field_spellings_accepted(self):
        self.raw(json.dumps({"node_id": "agent1", "cpu_percent": 5, "memory_percent": 6}).encode())
        self.assertEqual((self.node.cpu, self.node.mem), (5.0, 6.0))


class TestSequenceHandling(ServerTestBase):
    def test_gap_counts_as_loss(self):
        for q in (0, 1, 2, 5):
            self.send(q)
        self.assertEqual(self.node.lost, 2)

    def test_duplicate_is_ignored(self):
        self.send(0)
        self.send(1)
        self.send(1)
        self.assertEqual(self.node.reports, 2)
        self.assertEqual(self.node.duplicates, 1)
        self.assertEqual(read_csv(self.rpt)[-1]["flag"], "duplicate")

    def test_out_of_order_fills_gap_and_does_not_touch_state(self):
        for q in (0, 1, 4):
            self.send(q)
        self.assertEqual(self.node.lost, 2)
        self.send(2, cpu=99.0)          # late, and critical: must be ignored
        self.assertEqual(self.node.out_of_order, 1)
        self.assertEqual(self.node.lost, 1)
        self.assertEqual(self.node.status, m.OK)
        self.assertEqual(self.node.last_seq, 4)

    def test_restart_new_session_is_not_loss(self):
        for q in (0, 1, 2, 3):
            self.send(q, sid="AAA")
        for q in (0, 1):
            self.send(q, sid="BBB")
        self.assertEqual(self.node.restarts, 1)
        self.assertEqual(self.node.lost, 0)
        self.assertEqual(self.node.duplicates, 0)   # BBB seq 0/1 are not dups of AAA 0/1
        flags = [r["flag"] for r in read_csv(self.rpt)]
        self.assertEqual(flags.count("restart"), 1)

    def test_agent_without_session_id_still_works(self):
        for q in (3, 3, 0):
            self.send(q, sid=None)
        self.assertEqual(self.node.reports, 2)      # exact repeat dropped, reset accepted
        self.assertEqual(self.node.duplicates, 1)
        self.assertEqual(self.node.lost, 0)

    def test_delay_and_receive_time_are_logged(self):
        self.send(0)
        row = read_csv(self.rpt)[0]
        self.assertTrue(float(row["recv_ts"]) > float(row["send_ts"]) > 0)
        self.assertGreater(float(row["delay_ms"]), 0)
        self.assertEqual(row["report_id"], "agent1-S1-0")


class TestAlerts(ServerTestBase):
    def levels(self):
        return [a["level"] for a in self.mon.alerts]

    def test_critical_warning_normal_sequence(self):
        self.send(0)                       # OK
        self.send(1, cpu=95.0)             # -> CRITICAL
        self.send(2, cpu=80.0)             # -> WARNING (no longer critical)
        self.send(3, cpu=10.0)             # -> OK
        self.assertEqual(self.levels(), ["CRITICAL", "RECOVERED", "RECOVERED"])
        warn = list(self.mon.alerts)[1]
        self.assertIn("now WARNING", " ".join(warn["reasons"]))

    def test_memory_critical_and_agent_reported_critical(self):
        self.send(0, mem=95.0)
        self.assertEqual(self.levels(), ["CRITICAL"])
        self.send(1, sid="S1", node="agent2", status="critical")
        self.assertEqual(self.levels(), ["CRITICAL", "CRITICAL"])

    def test_alert_has_required_fields(self):
        self.send(0, cpu=95.0)
        a = list(self.mon.alerts)[0]
        for k in ("node_id", "src_ip", "level"):
            self.assertIn(k, a)
        self.assertEqual((a["node_id"], a["src_ip"], a["level"]), ("agent1", "10.0.0.1", "CRITICAL"))

    def test_critical_not_repeated_inside_cooldown(self):
        self.send(0, cpu=95.0)
        self.send(1, cpu=96.0)
        self.assertEqual(self.levels(), ["CRITICAL"])

    def test_critical_repeats_after_cooldown(self):
        self.mon.cfg["alerts"]["cooldown_sec"] = 0.0
        self.send(0, cpu=95.0)
        self.send(1, cpu=96.0)
        self.assertEqual(self.levels(), ["CRITICAL", "CRITICAL"])


class TestDownAndRejoin(ServerTestBase):
    heartbeat_timeout = 0.5

    def test_node_down_then_rejoin(self):
        threading.Thread(target=self.mon.heartbeat_loop, daemon=True).start()
        self.send(0)
        self.assertTrue(wait_for(lambda: self.node.status == m.DOWN))
        self.send(1)
        self.assertEqual(self.node.status, m.OK)
        levels = [a["level"] for a in self.mon.alerts]
        self.assertEqual(levels, ["NODE_DOWN", "RECOVERED"])
        events = [r["event"] for r in read_csv(self.evt)]
        self.assertIn("NODE_REJOIN", events)
        self.assertEqual(read_csv(self.rpt)[-1]["flag"], "rejoin")


class TestControllerDelivery(ServerTestBase):
    def start_with(self, fail_first):
        self.ctl = Controller(fail_first)
        self.addCleanup(self.ctl.stop)
        self.controller_url = self.ctl.url
        self.mon.shutdown()          # replace the monitor built in setUp()
        self.setUp()

    def test_critical_and_recovered_are_posted(self):
        self.start_with(0)
        self.send(0, cpu=95.0)
        self.assertTrue(wait_for(lambda: len(self.ctl.received) == 1))
        self.send(1, cpu=10.0)
        self.assertTrue(wait_for(lambda: len(self.ctl.received) == 2))
        self.assertEqual([a["level"] for a in self.ctl.received], ["CRITICAL", "RECOVERED"])
        for a in self.ctl.received:
            self.assertTrue({"node_id", "src_ip", "level"} <= set(a))

    def test_warning_alert_is_not_posted(self):
        self.start_with(0)
        self.send(0, cpu=80.0)
        time.sleep(0.4)
        self.assertEqual(self.ctl.received, [])

    def test_failed_post_is_retried(self):
        self.start_with(fail_first=2)
        self.send(0, cpu=95.0)
        self.assertTrue(wait_for(lambda: len(self.ctl.received) == 1))
        self.assertEqual(self.ctl.calls, 3)
        ev = [r for r in read_csv(self.evt) if r["event"] == "CONTROLLER_OK"][0]
        self.assertEqual(ev["attempts"], "3")

    def test_gives_up_after_retries(self):
        self.start_with(fail_first=99)
        self.send(0, cpu=95.0)
        self.assertTrue(wait_for(lambda: any(r["event"] == "CONTROLLER_FAILED"
                                             for r in read_csv(self.evt))))
        self.assertEqual(self.ctl.calls, 3)

    def test_stale_critical_is_not_retried_after_recovery(self):
        self.start_with(fail_first=99)
        self.send(0, cpu=95.0)
        self.send(1, cpu=10.0)             # recovered before the retry fires
        self.assertTrue(wait_for(lambda: any(r["event"] == "CONTROLLER_SKIPPED_STALE"
                                             for r in read_csv(self.evt))))


class TestUdpEndToEnd(ServerTestBase):
    def test_reports_arrive_on_both_ports(self):
        threading.Thread(target=self.mon.serve, daemon=True).start()
        self.assertTrue(wait_for(lambda: len(self.mon.sockets) == 2))
        ports = [s.getsockname()[1] for s in self.mon.sockets]
        self.assertEqual(len(set(ports)), 2)
        c = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        for i, p in enumerate(ports):
            c.sendto(json.dumps({"node_id": f"n{i}", "cpu": 5, "mem": 5, "seq": 0}).encode(),
                     ("127.0.0.1", p))
        self.assertTrue(wait_for(lambda: set(self.mon.nodes) == {"n0", "n1"}))
        self.assertEqual({r["port"] for r in read_csv(self.rpt)}, {str(p) for p in ports})

    def test_concurrent_agents(self):
        threading.Thread(target=self.mon.serve, daemon=True).start()
        self.assertTrue(wait_for(lambda: len(self.mon.sockets) == 2))
        port = self.mon.sockets[0].getsockname()[1]

        def agent(name):
            c = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            for q in range(20):
                c.sendto(json.dumps({"node_id": name, "cpu": 5, "mem": 5, "seq": q,
                                     "session_id": "S", "report_id": f"{name}-S-{q}"}).encode(),
                         ("127.0.0.1", port))
                time.sleep(0.005)

        ts = [threading.Thread(target=agent, args=(f"a{i}",)) for i in range(3)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertTrue(wait_for(lambda: len(self.mon.nodes) == 3
                                 and all(n.reports + n.lost >= 20 for n in self.mon.nodes.values())))


if __name__ == "__main__":
    unittest.main(verbosity=2)

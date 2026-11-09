#!/usr/bin/env python3
"""
run_experiments.py - automatic experiment runner (D2, Person 1)

For every (condition, trial) it:
  1. cleans up and starts the Ryu controller in the right mode (static/dynamic)
  2. starts the Mininet topology and feeds it the trial commands
  3. runs the 3 agents with the scenario (with or without background load)
  4. collects everything into  results/<condition>/trial<N>/

Conditions:
  A  no congestion                         (dynamic mode, no load)
  B  congestion + static forwarding        (static mode,  load)
  C  congestion + dynamic priority         (dynamic mode, load)
  D  recovery while congestion continues   (dynamic mode, load, critical-recovery-critical)

Run as root from the repo root:
    sudo python3 experiments/run_experiments.py --conditions C --trials 1 --results results/dry_run
    sudo python3 experiments/run_experiments.py --conditions A B C D --trials 3
"""
import argparse
import contextlib
import glob
import json
import os
import pwd
import shutil
import subprocess
import sys
import time
import urllib.request

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from parse_capture import parse_capture, write_csv, summarize  # noqa: E402

TMP = "/tmp/cn-integration"
STATUS_URL = "http://127.0.0.1:8080/status"
BOTTLENECK = 5000000          # bit/s on the server-facing port (fixed for all runs)
LOAD_RATE = "15M"             # requested iperf rate (3 streams)

CONDITIONS = {
    "A": {"label": "no congestion", "mode": "dynamic", "load": False,
          "scenario": "agent/scenarios/normal_critical_recovery.mn", "wait": 45},
    "B": {"label": "congestion, static", "mode": "static", "load": True,
          "scenario": "agent/scenarios/normal_critical_recovery.mn", "wait": 45},
    "C": {"label": "congestion, dynamic", "mode": "dynamic", "load": True,
          "scenario": "agent/scenarios/normal_critical_recovery.mn", "wait": 45},
    "D": {"label": "recovery under congestion", "mode": "dynamic", "load": True,
          "scenario": "agent/scenarios/recovery_under_load.mn", "wait": 55},
}


def sh(cmd):
    subprocess.run(cmd, shell=True, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL)


def cleanup():
    sh("mn -c")
    shutil.rmtree(TMP, ignore_errors=True)
    os.makedirs(TMP, exist_ok=True)


def get_status():
    try:
        with urllib.request.urlopen(STATUS_URL, timeout=2) as r:
            return json.load(r)
    except Exception:
        return None


def wait_for(check, timeout, what):
    end = time.time() + timeout
    while time.time() < end:
        try:
            if check():
                return
        except Exception:
            pass
        time.sleep(0.5)
    raise RuntimeError(f"timed out waiting for: {what}")


def start_ryu(mode, user, venv, log):
    cmd = (f"source {venv}/bin/activate && cd {REPO} && "
           f"HEALTH_MODE={mode} exec ryu-manager --ofp-tcp-listen-port 6653 "
           f"integration/health_controller.py")
    return subprocess.Popen(["sudo", "-u", user, "-H", "bash", "-c", cmd],
                            stdout=log, stderr=subprocess.STDOUT)


def start_topology(log):
    return subprocess.Popen(["python3", "topology.py"], cwd=REPO,
                            stdin=subprocess.PIPE, stdout=log,
                            stderr=subprocess.STDOUT, text=True)


def send(cli, lines):
    for line in lines:
        cli.stdin.write(line + "\n")
    cli.stdin.flush()


def setup_lines(c):
    lines = [
        "server pkill -TERM -f '^python3 -u d1_server/monitor_server.py'",
        "sh pkill -f iperf",
        "sh pkill -f tcpdump",
        "sh pkill -f agent.py",
        "sh rm -f results/agent_logs/*",
        f"sh bash integration/setup_queues.sh s1-eth4 {BOTTLENECK}",
        f"server python3 -u d1_server/monitor_server.py -c d1_server/mininet_config.json "
        f"--no-dashboard > {TMP}/server.log 2>&1 &",
        f"server iperf -s -u -p 6000 > {TMP}/iperf_server.txt 2>&1 &",
        'server tcpdump -i server-eth0 -n -tt -l -A -s 0 '
        f'"udp and (dst port 5000 or dst port 5001)" > {TMP}/capture.txt 2>/dev/null &',
        "sh sleep 2",
    ]
    if c["load"]:
        lines += [
            f"agent1 iperf -c 10.0.0.4 -u -p 6000 -b {LOAD_RATE} -l 1400 -P 3 "
            f"-t {c['wait'] + 20} > {TMP}/iperf_client.txt 2>&1 &",
            "sh sleep 5",
        ]
    lines.append(f"sh touch {TMP}/ready")
    return lines


def run_lines(c):
    scenario = os.path.join(REPO, c["scenario"])
    with open(scenario) as f:
        agent_lines = [l.strip() for l in f if l.startswith("agent")]
    if not agent_lines:
        raise RuntimeError(f"no agent lines found in {scenario}")
    return agent_lines + [
        f"sh sleep {c['wait']}",
        "sh pkill -f tcpdump",
        'sh pkill -f "iperf -c"',
        "server pkill -TERM -f '^python3 -u d1_server/monitor_server.py'",
        "sh sleep 2",
        f"sh touch {TMP}/done",
    ]


def collect(cond, trial, c, out, status_end):
    adir = os.path.join(out, "agent_logs")
    os.makedirs(adir, exist_ok=True)
    for f in glob.glob(os.path.join(REPO, "results/agent_logs/*")):
        shutil.copy(f, adir)
    for name in ("server.log", "capture.txt", "iperf_server.txt", "iperf_client.txt"):
        src = os.path.join(TMP, name)
        if os.path.exists(src):
            shutil.copy(src, os.path.join(out, name))

    rows = parse_capture(os.path.join(out, "capture.txt"))
    write_csv(rows, os.path.join(out, "recv_log.csv"))
    summary_path = os.path.join(out, "summary.txt")
    with open(summary_path, "w") as f, contextlib.redirect_stdout(f):
        print(f"{len(rows)} reports found in capture")
        summarize(adir, rows)

    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO,
                            capture_output=True, text=True).stdout.strip()
    with open(os.path.join(out, "run_info.txt"), "w") as f:
        f.write(f"condition: {cond} ({c['label']})\n")
        f.write(f"trial: {trial}\n")
        f.write(f"controller_mode: {c['mode']}\n")
        f.write(f"background_load: {'iperf UDP ' + LOAD_RATE + ' x3 streams, port 6000' if c['load'] else 'none'}\n")
        f.write(f"bottleneck_bit_per_s: {BOTTLENECK}\n")
        f.write(f"scenario_file: {c['scenario']}\n")
        f.write(f"git_commit: {commit}\n")
        f.write(f"finished: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
    with open(os.path.join(out, "status_end.json"), "w") as f:
        json.dump(status_end, f)

    print(open(summary_path).read())


def run_trial(cond, trial, args, user, venv):
    c = CONDITIONS[cond]
    out = os.path.join(REPO, args.results, cond, f"trial{trial}")
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out)
    print(f"\n=== condition {cond} ({c['label']}), trial {trial}, "
          f"mode {c['mode']} ===", flush=True)

    cleanup()
    ryu_log = open(os.path.join(out, "ryu.log"), "w")
    topo_log = open(os.path.join(out, "mininet.log"), "w")
    ryu = cli = ev = None
    status_end = None
    try:
        ryu = start_ryu(c["mode"], user, venv, ryu_log)
        wait_for(lambda: (get_status() or {}).get("mode") == c["mode"], 40,
                 f"controller in {c['mode']} mode")
        cli = start_topology(topo_log)
        wait_for(lambda: (get_status() or {}).get("switches"), 40,
                 "switch to connect to the controller")

        send(cli, setup_lines(c))
        wait_for(lambda: os.path.exists(f"{TMP}/ready"), 90, "trial setup to finish")

        ev = subprocess.Popen(
            ["python3", "integration/capture_evidence.py",
             "--seconds", str(c["wait"] + 10),
             "--output", os.path.join(out, "evidence.jsonl")],
            cwd=REPO, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        send(cli, run_lines(c))
        wait_for(lambda: os.path.exists(f"{TMP}/done"), c["wait"] + 60,
                 "scenario to finish")
        time.sleep(2)
        status_end = get_status()
        ev.wait(timeout=60)
    finally:
        if cli:
            try:
                cli.stdin.write("exit\n")
                cli.stdin.flush()
                cli.wait(timeout=20)
            except Exception:
                cli.kill()
        if ev and ev.poll() is None:
            ev.terminate()
        if ryu:
            ryu.terminate()
        ryu_log.close()
        topo_log.close()
        sh("mn -c")

    collect(cond, trial, c, out, status_end)


def give_back(path, user):
    """Files were created as root; hand them back to the normal user."""
    pw = pwd.getpwnam(user)
    for root, dirs, files in os.walk(path):
        for name in dirs + files:
            try:
                os.chown(os.path.join(root, name), pw.pw_uid, pw.pw_gid)
            except OSError:
                pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--conditions", nargs="+", default=["A", "B", "C", "D"],
                    choices=sorted(CONDITIONS))
    ap.add_argument("--trials", type=int, default=3)
    ap.add_argument("--results", default="results",
                    help="output folder, relative to the repo root")
    ap.add_argument("--venv", default=None, help="Ryu virtualenv (default ~/ryu-env)")
    args = ap.parse_args()

    if os.geteuid() != 0:
        raise SystemExit("Run as root: sudo python3 experiments/run_experiments.py ...")
    user = os.environ.get("SUDO_USER")
    if not user:
        raise SystemExit("Start it with sudo from your normal user (SUDO_USER is not set).")
    venv = args.venv or os.path.expanduser(f"~{user}/ryu-env")
    if not os.path.exists(os.path.join(venv, "bin", "ryu-manager")):
        raise SystemExit(f"ryu-manager not found in {venv}. Use --venv <path>.")
    os.chdir(REPO)

    try:
        for cond in args.conditions:
            for trial in range(1, args.trials + 1):
                run_trial(cond, trial, args, user, venv)
    finally:
        sh("mn -c")
        give_back(os.path.join(REPO, args.results), user)
        give_back(os.path.join(REPO, "results"), user)


if __name__ == "__main__":
    main()

# Remote System Health Monitoring

Three Python agents send health reports over UDP5000 (normal) and5001
(critical) to a concurrent monitoring server inside Mininet. Ryu controls OVS
over OpenFlow1.3/TCP6653. The server POSTs critical/recovery JSON to
`http://10.0.0.100:8080/alert`; priority rules select queue1 on the server port.

## D2 start commands (the handoff Person 1 requested)

Use the repo root in **every terminal**. Requires an Ubuntu Mininet/OVS VM,
system Python3 with psutil and a known working Ryu virtualenv. Activate your
actual environment path; the example below is Aditi's earlier environment.
Do not use the committed/copied historical virtualenv as a portable installer.

**Preparation, normal shell, before the test network starts:**

```bash
sudo mn -c
mkdir -p /tmp/cn-integration results/agent_logs results/person3
python3 -c 'import mininet, psutil'
```

**Terminal 1 - controller:**

```bash
source ~/ryu39-venv/bin/activate
HEALTH_MODE=dynamic ryu-manager --ofp-tcp-listen-port 6653 integration/health_controller.py
```

For a matched baseline, restart the whole test in the same setup using
`HEALTH_MODE=static` instead. No second `simple_switch_13` process is needed.
`HEALTH_LEASE_SECONDS` defaults to25; keep it above the server's10s repeat interval
with scheduling margin. Only the one-switch topology with server port4 is supported.

**Terminal 2 - topology:**

```bash
sudo python3 topology.py
```

**At `mininet>`** (no prompt prefix in pasted commands):

```text
pingall
sh bash integration/setup_queues.sh s1-eth4 10000000
server ping -c 1 10.0.0.100
server curl -sS http://10.0.0.100:8080/status
server python3 -u d1_server/monitor_server.py -c d1_server/mininet_config.json --no-dashboard > /tmp/cn-integration/server.log 2>&1 &
server cat /tmp/cn-integration/server.log
```

Expect12/12 ping delivery, controller `mode:dynamic` and a connected switch,
and server listeners on5000/5001. No `.254` link script is required: `topology.py`
already supplies root-namespace `ctl` at`.100`. Queue setup does not create links
or destroy other ports' QoS. Server settings are nested; root `config.json` is
for agents and is not a replacement for `d1_server/mininet_config.json`.

**Run Person 1's real agents, at `mininet>`:**

```text
source agent/scenarios/normal_critical_recovery.mn
```

This blocks the Mininet prompt for about45s. Before starting, use Terminal3 to
capture intermediate state (not just the final recovered state):

```bash
sudo python3 integration/capture_evidence.py --seconds 55 --output results/person3/normal-dynamic-1.jsonl
```

Other scenarios: `source agent/scenarios/two_critical.mn` (~45s) and
`source agent/scenarios/stop_restart.mn` (~65s; capture for75s).
Run one scenario at a time and save its logs before starting the next.
The scripts begin with Person 1's
`pkill -f agent.py`, so use a dedicated project VM without unrelated agents.
Do not run `agent/test_receiver.py` alongside the real server.

**Terminal 3 - inspect at any time:**

```bash
curl -sS http://127.0.0.1:8080/status
sudo ovs-ofctl -O OpenFlow13 dump-flows s1
sudo ovs-ofctl -O OpenFlow13 queue-stats s1
tail -n 30 /tmp/cn-integration/server.log
```

Critical rules match one sourceIP ->10.0.0.4, UDP5001, priority100,
`set_queue:1,output:4`. Each repeated critical alert refreshes a25s lease.
RECOVERED deletes only that node's rule, including critical->warning on Person2's
updated server. Missing recovery/down/crashed controller falls back to rule expiry;
this is bounded cleanup, not automatic service failover. `/status` is controller
state, not switch-installation acknowledgment; inspect actual flow/queue output.

**Stop:** let bounded agent scenarios finish, stop the server at `mininet>` with
`server pkill -TERM -f '^python3 -u d1_server/monitor_server.py'`, then `exit`.
Terminal1: Ctrl+C. Normal shell: `sudo mn -c`. Save `/tmp` logs before reboot.

## D2 VM results

- Normal, critical, and recovery: agent2's priority rule appeared during the
  critical phase. Queue 1 carried 9 packets and the rule was removed on recovery.
- Two critical agents: both rules appeared together in 14 samples. Agent3
  stayed prioritized after agent2 recovered.
- Stop and restart: agent3 was marked down after 6.1 seconds without a
  heartbeat and recovered with a new session ID.
- Lease expiry: the priority rule and controller entry cleared after the
  25-second lease expired.
- Static mode: the server forwarded critical alerts and none of the 65 switch
  samples contained a priority-100 rule.

The matched congestion performance comparison is pending.

See [agent setup and options](agent/README.md).

# UDP Monitoring Agent

The agent runs on each monitored node (agent1, agent2, agent3 in Mininet). Every 2 seconds it collects the machine's health statistics, marks them `normal` or `critical`, and sends one JSON report in a UDP datagram to the monitoring server (10.0.0.4).

- Normal reports go to **UDP 5000**.
- Critical reports go to **UDP 5001**, so the SDN controller can prioritize them.

## Files

| File | Purpose |
|---|---|
| `agent/agent.py` | Main agent: collect, classify, send, log |
| `agent/stats.py` | CPU, memory, process count and network counters (`psutil`) |
| `agent/udp_client.py` | UDP socket helpers and the shared `config.json` loader |
| `agent/test_receiver.py` | Tiny listener on ports 5000/5001 for testing without the real server |
| `agent/scenarios/*.mn` | Mininet scripts for repeatable test runs |
| `config.json` | Shared settings: server IP, ports, interval, thresholds |

## Requirements

Ubuntu, Python 3 and `psutil`:

```bash
sudo apt install python3-psutil
```

## Run one agent

From the repo root:

```bash
python3 agent/agent.py --id agent1
```

| Option | Default | Meaning |
|---|---|---|
| `--id` | required | Node ID (`agent1`, `agent2`, `agent3`) |
| `--server` | `server_ip` in `config.json` (10.0.0.4) | Server address (use `127.0.0.1` outside Mininet) |
| `--interval` | 2 | Seconds between reports |
| `--duration` | run until Ctrl+C | Stop automatically after N seconds |
| `--log-file` | off | Write a CSV sender log to this path |
| `--scenario` | off | Scripted run, e.g. `normal:10,critical:20,normal:10` (stops at the end) |
| `--critical-after`, `--critical-for` | off | Older demo option: fake critical CPU after N seconds, for M seconds |
| `--session-id` | random | Override the session ID (normally leave unset) |

A report is `critical` when CPU is above 85% or memory is above 90%. `--scenario` and `--critical-after` fake a high CPU value, so alerts can be triggered without real load.

## Report format

```json
{
  "node_id": "agent1",
  "session_id": "3f9a1c2e",
  "report_id": "agent1-3f9a1c2e-42",
  "seq": 42,
  "status": "normal",
  "cpu": 35.2, "mem": 61.0, "procs": 187,
  "net_sent": 123456, "net_recv": 98765,
  "ts": 1760000000.123456
}
```

| Field | Meaning |
|---|---|
| `session_id` | Random ID chosen when the agent starts. A new one means the agent restarted, so `seq` going back to 0 is a restart, not packet loss |
| `report_id` | `<node_id>-<session_id>-<seq>`, unique per report |
| `seq` | Counter starting at 0 in each session |
| `ts` | Unix time stamped right before sending (used for delay measurement) |
| `status` | `normal` or `critical` |

## Sender log (CSV)

With `--log-file`, the agent writes one row per report and flushes it immediately, so the log survives a killed agent:

`report_id, session_id, node_id, seq, ts_send, status, port, cpu, mem, send_ok, error`

At exit the agent prints a summary such as `agent2 session 302529b2 summary: sent_ok=20 failed=0 critical_sent=10`.

## Run inside Mininet

Start the controller and `topology.py` as described in the main README, then use the ready-made scenarios from the Mininet prompt (repo root):

```
mininet> server python3 -u agent/test_receiver.py > /tmp/recv.log 2>&1 &
mininet> source agent/scenarios/normal_critical_recovery.mn
```

(Skip the receiver line if the real monitoring server is running on the `server` host.)

| Scenario file | What happens | Takes |
|---|---|---|
| `normal_critical_recovery.mn` | agent2 goes normal, critical, normal; agent1 and agent3 stay normal | about 45 s |
| `two_critical.mn` | agent2 and agent3 are critical at the same time, overlapping about 15 s | about 45 s |
| `stop_restart.mn` | agent3 is killed, stays silent for 15 s, then restarts with a new session | about 65 s |

Each run writes its logs to `results/agent_logs/` (`agentN.csv` and `agentN.out`). Delete old logs with `rm -f results/agent_logs/*` before a new run, because the agent appends to existing files.

**Important:** in the Mininet CLI, a word that exactly matches a node name is replaced by that node's IP address. Typing `--id agent2` would start the agent with `--id 10.0.0.2`. Always write `--id=agent2` (with an equals sign and no space), as the scenario files do.

## Error handling

- A missing or invalid `config.json` stops the agent with a clear message.
- A failed stats read skips one report and the agent keeps running.
- A failed send is printed and written to the CSV (`send_ok=0`), and the agent keeps running. `seq` still increases, so the server can see the gap.
- Ctrl+C or `--duration` closes the socket and the log cleanly and prints the summary.

## Tests

| Test | How | Expected result | Result |
|---|---|---|---|
| Ports and destination | Three agents in Mininet, receiver on `server` | Reports from 10.0.0.1/.2/.3 arrive; normal on 5000, critical on 5001 | Pass |
| Normal, critical, recovery | `normal_critical_recovery.mn` | agent2 log shows 5 normal, 10 critical, 5 normal; others stay normal | Pass |
| Two agents critical at once | `two_critical.mn` | agent2 and agent3 critical with overlapping windows; agent1 normal | Pass |
| Stop and restart | `stop_restart.mn` | Two sessions for agent3, with a silent gap longer than 6 s between them | Pass |
| Restart detection | Run the same agent twice with one log file | Different `session_id`, `seq` restarts at 0 | Pass |
| Bad scenario text | `--scenario bogus` | Clear error message, no crash | Pass |
| Send failure | `--server not-a-real-host` | `send failed` printed, agent keeps running, CSV rows have `send_ok=0` | Pass |

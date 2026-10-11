# UDP Monitoring Agent

The agent runs on each monitored node (h1, h2, h3 in Mininet). Every few seconds it collects the machine's health statistics, classifies them as `normal` or `critical`, and sends them as a JSON report in one UDP datagram to the central monitoring server.

## Files

| File | Purpose |
|---|---|
| `agent/agent.py` | Main agent: collects stats, classifies, sends periodic reports |
| `agent/stats.py` | Collects CPU, memory, process count and network counters using `psutil` |
| `agent/udp_client.py` | UDP socket helpers and shared `config.json` loader |
| `config.json` | Shared settings (server IP, ports, interval, thresholds) used by all modules |

## Requirements

- Python 3
- `psutil`

```bash
sudo apt install python3-psutil
# or
pip3 install psutil --break-system-packages
```

## Configuration

All values come from the shared `config.json` in the repo root:

```json
{
  "server_ip": "10.0.0.4",
  "normal_port": 5000,
  "critical_port": 5001,
  "interval_sec": 2,
  "cpu_critical": 85,
  "mem_critical": 90,
  "heartbeat_timeout_sec": 6,
  "nodes": {
    "agent1": "10.0.0.1",
    "agent2": "10.0.0.2",
    "agent3": "10.0.0.3",
    "server": "10.0.0.4"
  }
}
```

## How to run

From the `agent/` folder:

```bash
python3 agent.py --id agent1
```

Options:

| Option | Default | Meaning |
|---|---|---|
| `--id` | required | Node ID (`agent1`, `agent2`, `agent3`) |
| `--server` | `server_ip` from config | Server IP address (use `127.0.0.1` for local testing) |
| `--interval` | `interval_sec` from config | Seconds between reports |
| `--critical-after` | off | Demo mode: fake a critical CPU value after N seconds |
| `--critical-for` | 10 | Demo mode: how many seconds the fake critical state lasts |

Inside Mininet (one agent per host):

```bash
mininet> h1 python3 agent/agent.py --id agent1 &
mininet> h2 python3 agent/agent.py --id agent2 &
mininet> h3 python3 agent/agent.py --id agent3 &
```

Press Ctrl+C to stop an agent cleanly.

## Report format

One JSON object per UDP datagram:

```json
{
  "node_id": "agent1",
  "seq": 42,
  "ts": 1760000000.123,
  "status": "normal",
  "cpu": 35.2,
  "mem": 61.0,
  "procs": 187,
  "net_sent": 123456,
  "net_recv": 98765
}
```

| Field | Meaning |
|---|---|
| `node_id` | Which agent sent the report |
| `seq` | Counter starting at 0, increases by 1 per report (lets the server detect lost packets) |
| `ts` | Unix time when the report was sent (used to measure alert latency) |
| `status` | `normal` or `critical` |
| `cpu`, `mem` | CPU and memory usage in percent |
| `procs` | Number of running processes |
| `net_sent`, `net_recv` | Cumulative bytes sent/received (from `psutil.net_io_counters()`) |

## Normal vs critical classification

A report is `critical` if CPU > `cpu_critical` (85%) or memory > `mem_critical` (90%). Otherwise it is `normal`.

The agent sends the two kinds of report to different UDP destination ports:

| Status | Destination port |
|---|---|
| normal | 5000 |
| critical | 5001 |

This lets the SDN controller identify and prioritize critical monitoring traffic with a simple match on the UDP destination port.

## Error handling

- A missing or invalid `config.json` stops the agent with a clear message.
- If collecting stats fails, that report is skipped and the agent keeps running.
- If sending fails (for example the network is unreachable), the error is printed and the agent keeps running. `seq` still increases, so the server can see the gap.
- Ctrl+C closes the socket cleanly.

## Testing

Local tests use a small receiver script (`test_receiver.py`) that listens on UDP ports 5000 and 5001 and prints every report.

| Test | How | Expected result | Result |
|---|---|---|---|
| Collect stats | `python3 stats.py` | CPU, memory, process count and network counters print | Pass |
| Send one message | `python3 udp_client.py --server 127.0.0.1` | Receiver prints the test JSON | Pass |
| Periodic reports | `python3 agent.py --id agent1 --server 127.0.0.1` | Report every 2 seconds, `seq` increases by 1 with no gaps | Pass |
| Normal/critical classification | Add `--critical-after 10 --critical-for 10` | First 10 s on port 5000 as `normal`, next 10 s on port 5001 as `critical`, then back to `normal` | Pass |
| Multiple agents | Run agent1, agent2, agent3 together, agent2 in critical demo mode | All three report concurrently with different source ports and independent `seq`; only agent2 turns critical | Pass |
| Node failure | Stop one agent with Ctrl+C | Its reports stop while the others continue; after 6 s of silence the server should mark it DOWN | Pass |
| Send error handling | Run with `--server not-a-real-host` | Agent prints `send failed` and keeps running | Pass |

Screenshots are in `docs/screenshots/`.

# Person 4: Socket–SDN Integration

Connects application-level health alerts from the monitoring server to the
SDN controller, so traffic from a node in a critical state is moved to a
high-priority queue, and moved back when it recovers.

## Files

| File | Purpose |
|---|---|
| `integration/health_controller.py` | Ryu app: learning switch + alert REST API + dynamic flow rules |
| `integration/setup_queues.sh` | Creates the normal and high-priority queues on `s1-eth4` |
| `integration/setup_controller_link.sh` | Gives the Mininet hosts a path to the Ryu REST API at `10.0.0.254` |
| `integration/server_config.json` | Enables controller notification in the monitoring server |

## 1. Critical-event communication

The monitoring server (`d1_server/monitor_server.py`) raises an alert when a node
crosses a threshold (CPU > 85% or memory > 90%) and when it recovers. For
`CRITICAL` and `RECOVERED` alerts it sends an HTTP POST with a JSON body to the
controller:

```
POST http://10.0.0.254:8080/alert
Content-Type: application/json
```

Main fields of the JSON body:

| Field | Meaning |
|---|---|
| `level` | `CRITICAL` or `RECOVERED` (other levels, e.g. `WARNING`, are ignored by the controller) |
| `node_id` | Identifier of the node that raised the alert |
| `src_ip` | IP address of the node, e.g. `10.0.0.2` |
| `reasons` | List of strings, e.g. `["CPU 95.0% > 85.0%"]` |

`10.0.0.254` is the VM side of a virtual link created by
`setup_controller_link.sh`. Hosts inside Mininet cannot use `127.0.0.1` to reach
Ryu, because each host has its own network namespace.

## 2. Controller interface

`health_controller.py` is one Ryu app (OpenFlow 1.3) with two jobs:

1. A basic learning switch, so all hosts can communicate.
2. A REST interface on port 8080:

| Endpoint | Method | Purpose |
|---|---|---|
| `/alert` | POST | Receive an alert from the monitoring server |
| `/status` | GET | List the nodes that currently have a priority rule |

Behaviour on `POST /alert`:

- `CRITICAL`: install a priority flow rule for that node (if not already installed).
- `RECOVERED`: delete that rule.
- Missing `src_ip` returns 400. Invalid JSON returns 400. No connected switch returns 503.
- Repeated `CRITICAL` alerts for the same node are ignored (`already_prioritized`).

## 3. Queues

`setup_queues.sh` creates a Linux HTB QoS configuration on the switch port facing
the server (`s1-eth4`, link limit 10 Mbit/s):

| Queue | Role | Min rate | Max rate | Priority |
|---|---|---|---|---|
| 0 | Normal traffic | 1 Mbit/s | 10 Mbit/s | 1 |
| 1 | High priority (critical) | 8 Mbit/s | 10 Mbit/s | 0 (higher) |

## 4. Dynamic flow rules

On `CRITICAL`, the controller installs this rule on `s1`:

```
priority=100, udp, nw_src=<node IP>, nw_dst=10.0.0.4, tp_dst=5001
actions=set_queue:1,output:4
```

- `10.0.0.4` is the server, UDP `5001` is the critical-report port, and port `4`
  is the switch port facing the server.
- Priority 100 is above the learning-switch rules (priority 1), so it wins for
  matching traffic.
- On `RECOVERED`, the rule is deleted (strict delete on the same match and priority).

## How to run

Use four terminals from the repo root.

1. **Terminal 1, controller:**
   ```
   source ~/ryu-env/bin/activate
   ryu-manager integration/health_controller.py
   ```
2. **Terminal 2, Mininet:**
   ```
   sudo python3 topology.py
   ```
   Wait until Terminal 1 prints `switch 1 connected`. Then, at the `mininet>` prompt:
   ```
   pingall
   sh bash integration/setup_queues.sh
   sh bash integration/setup_controller_link.sh
   server ping -c1 10.0.0.254
   server python3 d1_server/monitor_server.py -c integration/server_config.json > /tmp/server.log 2>&1 &
   agent1 python3 agent/agent.py --id agent1 --server 10.0.0.4 > /tmp/a1.log 2>&1 &
   ```
3. **Terminal 3, status check (plain shell):**
   ```
   curl -s http://127.0.0.1:8080/status; echo
   ```
4. **Trigger a critical alert (Terminal 2):**
   ```
   agent2 python3 agent/agent.py --id agent2 --server 10.0.0.4 --critical-after 1 --critical-for 180 > /tmp/a2.log 2>&1 &
   ```

Verification commands (Terminal 2):
```
sh ovs-ofctl -O OpenFlow13 dump-flows s1 | grep priority=100
sh ovs-ofctl -O OpenFlow13 queue-stats s1
```

Cleanup after a run: type `exit` in Mininet, then `sudo mn -c`.

## Test results

| Step | Observed result |
|---|---|
| Before the alert | `dump-flows \| grep priority=100` prints nothing; queue 1 has 0 packets |
| Alert sent | Controller log: `CRITICAL 10.0.0.2 (10.0.0.2): priority rule installed, queue 1`; `POST /alert` returned 200 |
| Controller state | `GET /status` lists `10.0.0.2` with reason `CPU 95.0% > 85.0%` |
| Flow rule installed | `priority=100,udp,nw_src=10.0.0.2,nw_dst=10.0.0.4,tp_dst=5001 actions=set_queue:1,output:4` (`n_packets=28`) |
| Queue statistics | Queue 1 packets rose from 0 to 32 to 54 while the node was critical; queue 0 continued to carry normal traffic |
| Recovery | Controller log: `RECOVERED 10.0.0.2 (10.0.0.2): priority rule removed`; the priority-100 rule no longer appears in `dump-flows` |

Screenshots are in `docs/person4/screenshots/`:
- `1_before_during_after_mininet.png`: flow and queue statistics
- `2_ryu_log.png`: controller log from `CRITICAL` to `RECOVERED`
- `3_status.png`: `GET /status` output

## Notes

- The controller was tested with one switch (`s1`) and the server on port 4.
  `SERVER_PORT_NO` in `health_controller.py` must match the switch port that faces the server.
- If `topology.py` later adds a NAT node, `setup_controller_link.sh` can be dropped
  and the URL in `server_config.json` pointed at the NAT address.

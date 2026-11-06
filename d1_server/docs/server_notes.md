# Central monitoring server (Person 2)

`d1_server/monitor_server.py` receives UDP health reports from the agents, tracks every
node, raises alerts, tells the SDN controller about critical nodes, and writes CSV logs.

## Run

Standalone (any machine):
```bash
python3 d1_server/monitor_server.py -c d1_server/config.json
```
Inside Mininet (host `server`, controller enabled):
```bash
mkdir -p /tmp/cn-integration
server python3 -u d1_server/monitor_server.py -c d1_server/mininet_config.json --no-dashboard \
    > /tmp/cn-integration/server.log 2>&1 &
```
Options: `-c FILE` config, `--port N` listen on one port only, `--no-dashboard` log-only mode.
Stop with Ctrl+C or `kill` (SIGTERM).

## What it does

| Feature | Behaviour |
|---|---|
| Ports | Listens on UDP 5000 (normal) and 5001 (critical); each port has its own thread and reports are handled by a worker pool, so agents are served concurrently. |
| Validation | A report must be a JSON object with `node_id`, and `cpu`/`mem` (or `cpu_percent`/`memory_percent`). Anything else is dropped, counted as malformed and logged. |
| Node status | `OK`, `WARNING` (CPU > 75 or MEM > 75), `CRITICAL` (CPU > 85, MEM > 90, or agent reports `critical`/`high`), `DOWN` (no report for `heartbeat.timeout_sec`, default 6 s). Thresholds are in the config file. |
| Duplicates | A repeated `report_id` (or `seq`) within a session is ignored and counted. |
| Out-of-order | A report older than the newest `seq` is ignored (it does not change node state) and counted; if it fills a gap already counted as lost, the lost count goes down by one. |
| Restarts | A new `session_id` from the same node means the agent restarted. `seq` tracking resets, so the reset is not counted as loss. Agents without `session_id` still work (only exact repeats are treated as duplicates). |
| Timestamps | Receive time is taken when the datagram arrives. The agent's send time `ts` is kept, and `delay_ms = recv - send` (both on the same VM clock). |
| Dashboard | Live terminal table: status, CPU, MEM, processes, TX/RX rates, reports, lost, duplicates, out-of-order, age, plus recent alerts. |

## Alerts and the controller

Alert levels: `WARNING`, `CRITICAL`, `RECOVERED`, `NODE_DOWN`.

* `CRITICAL` is sent when a node becomes critical, and again every `alerts.cooldown_sec` (10 s) while it stays critical.
* `RECOVERED` is sent when a node stops being critical (to OK **or to WARNING**) and when a DOWN node returns.
  Going CRITICAL -> WARNING -> OK therefore sends two `RECOVERED` alerts; the controller must treat a repeated `RECOVERED` as harmless.
* Only `CRITICAL` and `RECOVERED` are POSTed to the controller (`controller.url`, default `http://10.0.0.100:8080/alert`).

Alert JSON:
```json
{"time": "2026-11-03T10:15:02", "epoch": 1793935028.08, "node_id": "agent1",
 "src_ip": "10.0.0.1", "level": "CRITICAL", "reasons": ["CPU 95.0% > 85.0%"],
 "cpu_percent": 95.0, "memory_percent": 20.0}
```
A failed POST is retried (`controller.retries` = 3 tries, `retry_delay_sec` = 1 s apart). A retry is
skipped if the node's state has changed meanwhile (a CRITICAL alert for a node that already recovered,
or a RECOVERED for a node that is critical again). The next periodic CRITICAL gives the controller another chance.

## Logs (`logging` section of the config)

* `alerts.log` - human-readable server log.
* `reports.csv` - one row per received datagram:
  `recv_ts, send_ts, delay_ms, node_id, src_ip, port, session_id, seq, report_id, cpu, mem, agent_status, node_status, flag`
  where `flag` is `ok`, `duplicate`, `out_of_order`, `restart`, `rejoin` or `malformed`.
* `events.csv` - `epoch, time, event, node_id, src_ip, level, detail, attempts, latency_ms` with events
  `ALERT`, `AGENT_RESTART`, `NODE_REJOIN`, `MALFORMED`, `CONTROLLER_OK` (attempts and latency), `CONTROLLER_FAILED`, `CONTROLLER_SKIPPED_STALE`.

In Mininet all three are written to `/tmp/cn-integration/`. Files are appended to, so delete them between experiment runs.

## Tests

```bash
python3 -m unittest discover -s d1_server/tests -v
```
22 tests (about 3 s, no root or Mininet needed): malformed reports, sequence gaps, duplicates,
out-of-order, restart, agents without session id, CSV contents, critical -> warning -> normal,
DOWN and rejoin, controller delivery, retry, give-up, stale-alert skip, UDP on both ports, and 3 concurrent agents.

## Known limitations

* A node that goes DOWN while critical sends no `RECOVERED`, so the controller's priority rule stays until the node reports again.
* DOWN -> WARNING sends only a `WARNING` alert, which is not forwarded to the controller.
* `ts` delay figures are only meaningful when agents and server share a clock (true in one Mininet VM).

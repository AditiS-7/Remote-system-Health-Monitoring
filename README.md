# Remote System Health Monitoring using SDN

## 1. Introduction

This project is a Remote System Health Monitoring system built using Python, UDP socket programming, Mininet, Open vSwitch (OVS) and the Ryu SDN controller.

The system monitors three simulated agents (agent1, agent2 and agent3). Each agent periodically collects its CPU usage, memory usage, process count and network statistics and sends the information to a central monitoring server.

The server analyses the received reports and identifies the status of each agent. If an agent becomes critical, the server sends an alert to the Ryu controller. The controller then installs a flow rule in the OVS switch to give critical traffic higher priority.

When the agent recovers, the controller removes the priority rule and normal forwarding continues.

The main aim is to demonstrate how SDN can be used to improve the delivery of important monitoring information during network congestion.

## 2. Technologies Used

- **Python 3:** Agent implementation, monitoring server and experiment scripts.
- **UDP sockets:** Communication between agents and the monitoring server.
- **Mininet:** Creation of the virtual network topology.
- **Open vSwitch (OVS):** Software switch used for packet forwarding.
- **Ryu Controller:** Controls the switch using OpenFlow 1.3.
- **Linux HTB Queues:** Used to allocate bandwidth to normal and critical traffic.
- **iperf:** Generates background UDP traffic for congestion experiments.
- **tcpdump:** Captures packets and records their arrival times.
- **psutil:** Collects system health statistics.

## 3. System Architecture

The system consists of three main components:

### A. Monitoring Agents

Three agents run inside Mininet:

- agent1 – 10.0.0.1
- agent2 – 10.0.0.2
- agent3 – 10.0.0.3

Each agent collects system information every 2 seconds and sends a JSON report to the monitoring server.

The report contains:
- Node ID
- Session ID
- Report ID and sequence number
- CPU usage
- Memory usage
- Process count
- Network bytes sent and received
- Timestamp
- Current status

Normal reports are sent through UDP port 5000, while critical reports are sent through UDP port 5001.

An agent is classified as critical when CPU usage exceeds 85% or memory usage exceeds 90%.

### B. Monitoring Server

The monitoring server runs at 10.0.0.4 and listens on UDP ports 5000 and 5001.

It performs the following tasks:

- Receives reports from all three agents.
- Maintains the latest information for each agent.
- Classifies agents as OK, WARNING, CRITICAL or DOWN.
- Tracks sequence numbers and identifies missing reports.
- Detects agent restarts using session IDs.
- Detects missing heartbeats and marks an agent DOWN after 6 seconds without a report.
- Sends CRITICAL and RECOVERED alerts to the Ryu controller.
- Maintains logs and displays the monitoring information through a terminal dashboard.

The warning thresholds are 75% CPU and 75% memory. The critical thresholds are 85% CPU and 90% memory.

### C. SDN Controller and Switch

The Ryu controller communicates with the Open vSwitch switch using OpenFlow 1.3 over TCP port 6653.

The controller also provides a REST API on port 8080.

When the monitoring server detects a critical agent, it sends an HTTP POST request to:

`http://10.0.0.100:8080/alert`

The controller installs a priority-100 flow rule for the critical agent's traffic.

The rule matches:
- Source IP of the critical agent
- Destination IP of the monitoring server
- UDP destination port 5001

The matching packets are directed to queue 1.

When the agent recovers, the controller removes its priority rule. If a recovery alert is not received, the rule is automatically removed after the configured lease expires (25 seconds by default).

The controller supports both static and dynamic modes. In static mode, critical alerts are received but do not install dynamic priority rules.

### Architecture Overview

```text
       Agent 1       Agent 2       Agent 3
          |             |             |
          |             |             |
          +-------------+-------------+
                        |
                 UDP Reports
              Port 5000 / 5001
                        |
                        v
              Monitoring Server
                 10.0.0.4
                        |
                  HTTP Alerts
               Port 8080 (/alert)
                        |
                        v
                Ryu Controller
                10.0.0.100
                        |
                 OpenFlow 1.3
                  TCP Port 6653
                        |
                        v
                 OVS Switch (s1)
                        |
              +---------+---------+
              |                   |
           Queue 0              Queue 1
        Normal Traffic       Critical Traffic
```

## 4. Network Topology

The project uses one Open vSwitch switch (s1) connected to three agents and one monitoring server.

| Node | IP Address | Purpose |
|---|---|---|
| agent1 | 10.0.0.1 | Monitoring agent |
| agent2 | 10.0.0.2 | Monitoring agent |
| agent3 | 10.0.0.3 | Monitoring agent |
| server | 10.0.0.4 | Central monitoring server |
| ctl | 10.0.0.100 | Controller communication interface |

The `ctl` node provides connectivity between the monitoring server inside Mininet and the Ryu controller running on the host VM.

The server-facing switch port is `s1-eth4`.

Two queues are configured on this port:

| Queue | Traffic | Minimum bandwidth |
|---|---|---:|
| Queue 0 | Normal traffic | 10% |
| Queue 1 | Critical traffic | 80% |

The remaining bandwidth is available according to the queue configuration and traffic conditions.

## 5. Repository Structure

```text
Remote-system-Health-Monitoring/
│
├── agent/
│   ├── agent.py
│   ├── stats.py
│   ├── udp_client.py
│   ├── test_receiver.py
│   ├── README.md
│   └── scenarios/
│       ├── normal_critical_recovery.mn
│       ├── two_critical.mn
│       ├── stop_restart.mn
│       └── recovery_under_load.mn
│
├── d1_server/
   │   ├── monitor_server.py
   │   ├── mininet_config.json
   │   ├── fake_agent.py          (test helper: simulates agents for server testing)
   │   ├── tests/
   │   │   └── test_server.py     (22 unit tests)
   │   └── docs/
   │       └── server_notes.md
   │
   ├── integration/
   │   ├── health_controller.py
   │   ├── setup_queues.sh
   │   ├── cleanup_queues.sh
   │   └── capture_evidence.py
   │├── experiments/
│   ├── run_experiments.py
│   └── parse_capture.py
│
   ├── docs/
   │   ├── person4/               (controller integration notes)
   │   ├── screenshots/
   │   └── evidence/
   │       ├── network/           (flow and controller captures)
   │       └── scenario-runs/     (agent logs and switch captures per scenario)
   │
   ├── results/
   │   ├── A/  B/  C/  D/         (experiment trials)
   │   ├── per_trial.csv
   │   ├── summary.csv
   │   └── performance.svg
   │
├── topology.py
├── config.json
└── README.md
```

## 6. Setup and Execution

### Requirements

The project is intended to run on an Ubuntu VM with:

- Python 3
- Mininet
- Open vSwitch
- Ryu 4.34
- Python 3.9 virtual environment for Ryu
- psutil
- iperf2
- tcpdump
- curl

The Ryu controller should be run from its working Python 3.9 virtual environment because of compatibility requirements.

### Step 1: Clone the repository

```bash
git clone https://github.com/AditiS-7/Remote-system-Health-Monitoring.git
cd Remote-system-Health-Monitoring
```

### Step 2: Prepare the environment

Make sure the required packages and Ryu environment are installed.

For the normal Python environment:

```bash
sudo apt update
sudo apt install mininet openvswitch-switch iperf tcpdump curl python3-psutil
```

Activate the Ryu virtual environment. The example below assumes it is located at `~/ryu39-venv`. If a different path was used during setup, use that path instead.

```bash
source ~/ryu39-venv/bin/activate
ryu-manager --version
```

### Step 3: Clean up previous Mininet sessions

Before starting the project:

```bash
sudo mn -c
mkdir -p /tmp/cn-integration results/agent_logs
```

### Step 4: Start the Ryu controller

Open Terminal 1:

```bash
cd ~/Remote-system-Health-Monitoring
source ~/ryu39-venv/bin/activate

HEALTH_MODE=dynamic ryu-manager --ofp-tcp-listen-port 6653 integration/health_controller.py
```

The controller starts in dynamic mode.

For static forwarding experiments, use `HEALTH_MODE=static` instead.

### Step 5: Start the Mininet topology

Open Terminal 2:

```bash
cd ~/Remote-system-Health-Monitoring
sudo python3 topology.py
```

At the Mininet prompt, test connectivity:

```text
pingall
```

All 12 host-to-host ping tests should succeed.

### Step 6: Configure the queues

At the `mininet>` prompt:

```text
sh bash integration/setup_queues.sh s1-eth4 10000000
```

This configures a 10 Mbps total rate on the server-facing port, with separate queues for normal and critical traffic.

### Step 7: Check controller connectivity

```text
server ping -c 1 10.0.0.100
server curl -sS http://10.0.0.100:8080/status
```

The controller should report dynamic mode and a connected switch.

### Step 8: Start the monitoring server

At the Mininet prompt:

```text
server python3 -u d1_server/monitor_server.py -c d1_server/mininet_config.json --no-dashboard > /tmp/cn-integration/server.log 2>&1 &
```

Check the server log:

```text
server cat /tmp/cn-integration/server.log
```

The server should listen on UDP ports 5000 and 5001.

## 7. Functional Testing and Scenarios

The project includes three main scenarios to test the monitoring and SDN functionality.

### Scenario 1: Normal, Critical and Recovery

File: `agent/scenarios/normal_critical_recovery.mn`

This scenario keeps agent1 and agent3 in the normal state while agent2 changes its status.

The sequence is:

1. Agent2 sends normal reports.
2. Agent2 enters the critical state.
3. Critical reports are sent through UDP port 5001.
4. The monitoring server sends a CRITICAL alert to Ryu.
5. Ryu installs a priority-100 rule for agent2.
6. Agent2 returns to normal.
7. The server sends a RECOVERED alert.
8. Ryu removes agent2's priority rule.

Run from the Mininet prompt:

```text
source agent/scenarios/normal_critical_recovery.mn
```

The scenario takes approximately 45 seconds.

### Scenario 2: Two Agents Critical Simultaneously

File: `agent/scenarios/two_critical.mn`

This scenario tests whether the controller can handle multiple critical agents at the same time.

The sequence is:

1. Agent2 and agent3 enter the critical state with overlapping critical periods.
2. The server sends alerts for both agents.
3. Ryu installs separate priority rules for both agents.
4. Agent2 recovers.
5. Agent3 remains prioritised until it also recovers.

Run:

```text
source agent/scenarios/two_critical.mn
```

The scenario takes approximately 45 seconds.

### Scenario 3: Agent Stop and Restart

File: `agent/scenarios/stop_restart.mn`

This scenario checks how the monitoring server handles an agent that suddenly stops sending reports.

The sequence is:

1. All three agents start sending reports.
2. Agent3 is stopped.
3. The server detects that no heartbeat has been received for more than 6 seconds.
4. Agent3 is marked DOWN.
5. Agent3 is started again.
6. The new session ID indicates that the agent has restarted.

Run:

```text
source agent/scenarios/stop_restart.mn
```

The scenario takes approximately 65 seconds.

### Checking the controller and switch

While the scenarios are running, use another terminal to inspect the controller:

```bash
curl -sS http://127.0.0.1:8080/status
```

Check installed flows:

```bash
sudo ovs-ofctl -O OpenFlow13 dump-flows s1
```

Check queue statistics:

```bash
sudo ovs-ofctl -O OpenFlow13 queue-stats s1
```

Check the monitoring server:

```bash
tail -n 30 /tmp/cn-integration/server.log
```

A priority-100 rule with `set_queue:1` should appear during the critical phase and disappear after recovery.

The queue statistics can be used to verify that critical traffic was forwarded through queue 1.

## 8. Evidence Collection

The project includes an evidence capture script that periodically records controller status, switch flows and queue statistics.

Start evidence capture from a separate terminal before running a scenario.

For normal-critical-recovery:

```bash
sudo python3 integration/capture_evidence.py --seconds 55 --output results/normal-critical-recovery.jsonl
```

For two simultaneous critical agents:

```bash
sudo python3 integration/capture_evidence.py --seconds 55 --output results/two-critical.jsonl
```

For agent stop and restart:

```bash
sudo python3 integration/capture_evidence.py --seconds 75 --output results/stop-restart.jsonl
```

The scenario should be started from the Mininet prompt while the capture script is running.

The evidence files contain snapshots that can be used to check:
- Controller operating mode
- Switch connection
- Installed priority flows
- Queue statistics
- Changes during critical and recovery phases

The agent logs are stored in `results/agent_logs/`. The monitoring server logs and alert logs are stored under `/tmp/cn-integration/`.

## 9. Performance Evaluation

The performance evaluation compares the delivery delay of critical monitoring reports under different network conditions.

The experiment runner automatically:
- Starts the Ryu controller in the required mode.
- Starts the Mininet topology.
- Configures the server-facing bottleneck.
- Starts the monitoring server.
- Generates background UDP traffic when required.
- Runs the selected agent scenario.
- Captures packets using tcpdump.
- Matches received reports with sender logs using report IDs.
- Calculates packet loss and report delivery delay.
- Saves the results for each trial.

### Experimental Conditions

Four conditions are used.

| Condition | Network condition | Controller mode | Purpose |
|---|---|---|---|
| A | No congestion | Dynamic | Reference performance |
| B | Congestion | Static | Baseline without dynamic prioritisation |
| C | Congestion | Dynamic | Effect of critical traffic prioritisation |
| D | Congestion with repeated critical and recovery phases | Dynamic | Recovery behaviour under continuing congestion |

For the congested conditions, the experiment runner uses:
- A 5 Mbps bottleneck on the server-facing port.
- UDP background traffic generated using iperf.
- Three iperf streams on port 6000.
- A requested background traffic rate of 15 Mbps.

Conditions B and C use the same congestion setup. The main difference is the controller mode.

### Running the Experiments

Run from the repository root:

```bash
sudo python3 experiments/run_experiments.py --conditions A B C D --trials 3 --venv "$HOME/ryu39-venv"
```

If the Ryu environment is named `ryu-env`, replace the path accordingly.

To run only conditions B and C:

```bash
sudo python3 experiments/run_experiments.py --conditions B C --trials 1 --results results/demo --venv "$HOME/ryu39-venv"
```

**Note:** The experiment runner removes and recreates the output folder for each condition and trial. Use a separate `--results` folder if you want to preserve existing results.

### Result Files

Each trial stores its output in a separate folder.

For example:

```text
results/
├── A/
│   ├── trial1/
│   ├── trial2/
│   └── trial3/
├── B/
├── C/
└── D/
```

Each trial folder contains files such as:

- `agent_logs/` – sender-side CSV and output logs.
- `capture.txt` – tcpdump packet capture.
- `recv_log.csv` – received report timestamps.
- `summary.txt` – sent, received, loss and delay statistics.
- `server.log` – monitoring server output.
- `iperf_client.txt` and `iperf_server.txt` – background traffic logs, when applicable.
- `evidence.jsonl` – controller, flow and queue snapshots.
- `run_info.txt` – experimental configuration.
- `status_end.json` – controller status at the end of the trial.

### Performance Results

The following results are from the three recorded trials for each condition in the repository.

The values below represent the mean of the three per-trial average delays, along with the sample standard deviation.

| Condition | Critical report delay (mean ± SD) |
|---|---:|
| A – No congestion | 0.20 ± 0.07 ms |
| B – Congestion, static forwarding | 481.61 ± 21.81 ms |
| C – Congestion, dynamic priority | 48.49 ± 5.65 ms |
| D – Recovery under congestion | 64.67 ± 3.91 ms |

The normal-report delays for agent2 were:

| Condition | Normal report delay (mean ± SD) |
|---|---:|
| A | 3.03 ± 0.67 ms |
| B | 499.75 ± 20.95 ms |
| C | 513.29 ± 13.59 ms |
| D | 507.29 ± 9.27 ms |

### Analysis

The most important comparison is between conditions B and C.

Both conditions use the same congestion setup, but condition B uses static forwarding while condition C uses dynamic prioritisation.

The average critical-report delay decreases from approximately 481.61 ms to 48.49 ms.

This is a reduction of approximately **89.9%**.

The results show that dynamic prioritisation significantly improves the delivery delay of critical monitoring reports during congestion.

Normal traffic continues to experience high delay under congestion. This is expected because the priority rule is specifically intended to improve critical traffic rather than all traffic.

All critical reports recorded in the 12 trials were received, with 0% recorded critical-report loss.

## 10. Important Implementation Details

- Agents send reports every 2 seconds.
- Normal reports use UDP port 5000.
- Critical reports use UDP port 5001.
- CPU critical threshold is above 85%.
- Memory critical threshold is above 90%.
- Warning thresholds are above 75% for CPU and memory.
- The server marks an agent DOWN after 6 seconds without a heartbeat.
- A new session ID is used to identify an agent restart.
- The controller uses priority 100 for critical traffic.
- Queue 1 is selected for critical reports.
- The default controller priority-rule lease is 25 seconds.
- The controller communicates with OVS using OpenFlow 1.3.

The manual functional demo uses a 10 Mbps queue configuration, whereas the automated performance experiments use a 5 Mbps bottleneck. These are separate test configurations.

## 11. Limitations

- The agents run as simulated hosts inside Mininet rather than monitoring separate physical machines.
- The performance results depend on the available resources of the host VM.
- The current topology is designed around one OVS switch and the configured server-facing port.
- The controller lease provides automatic removal of stale priority rules, but it does not provide automatic controller failover.
- The system uses UDP for periodic reports, so delivery is not guaranteed by the transport protocol.
- The performance measurements are based on the recorded Mininet experiments and may differ on a physical network.

## 12. Conclusion

The project demonstrates a complete monitoring system using UDP socket programming integrated with an SDN-controlled network.

Multiple agents send periodic health reports to a central server. The server identifies abnormal conditions and communicates with the Ryu controller when critical traffic needs prioritisation.

Using OpenFlow rules and separate queues, the system gives critical reports higher priority during network congestion. The experiments show a significant reduction in critical-report delivery delay when dynamic prioritisation is enabled.

The project also tests multiple simultaneous critical agents, agent failure and restart, recovery behaviour and automatic expiry of stale priority rules.

Overall, the implementation demonstrates how socket programming and SDN can work together to provide more responsive network monitoring.

## 13. References

- Mininet: https://mininet.org/
- Open vSwitch: https://www.openvswitch.org/
- Ryu SDN Framework: https://ryu-sdn.org/
- Python Socket Programming: https://docs.python.org/3/library/socket.html
- psutil: https://psutil.readthedocs.io/
- iperf: https://iperf.fr/

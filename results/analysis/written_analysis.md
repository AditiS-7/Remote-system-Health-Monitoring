# Analysis of the experiments (draft - edit in your own words)

Numbers come from `results/analysis/summary.csv` (12 trials: 4 conditions x 3 trials). Values are the mean and
sample SD of the per-trial average delay. Delay = time the report reached the server (tcpdump on `server-eth0`)
minus the time the agent sent it, both on the same VM clock.

## What improved

- **Congestion hurts critical reports a lot.** With no congestion (A) a critical report takes 0.20 ms. With
  15 Mbps of background UDP on a 5 Mbps bottleneck and plain forwarding (B) it takes 481.6 ms.
- **Dynamic priority removes most of that delay.** With the same congestion but dynamic priority (C) the
  critical delay is 48.5 ms, a 89.9% reduction compared with B. The SD is small (5.65 ms against 21.81 ms in B), so
  the three trials agree. B and C differ only in the controller mode.
- **It also works when the node recovers during congestion (D).** Critical delay stays low at 64.7 ms while
  the priority rule is installed, removed and installed again.
- **No critical reports were lost** in any trial (all 129 critical reports sent were received), and the priority flow
  (priority 100, `set_queue:1`) was seen in conditions A, C and D and never in B. Queue 1 counters rose only when a
  rule was active (up to 9 packets in A and C, 11 in D).

## What did not improve

- **Normal reports stay slow under congestion**: about 500 ms in B, 513 ms in C, 507 ms in D. The rule only
  moves the critical node's port-5001 traffic to queue 1, so normal traffic still waits behind the background load.
  This is intended, but it should be stated.
- **Loss is not a useful comparison here.** All conditions show 0% loss because the queue is deep enough to hold
  the reports; the effect of congestion shows up as delay, not loss.

## Rule timing (controller view)

From `evidence.jsonl` (snapshots about once a second) the rule appears about 1.5 s after the first critical
report was sent under congestion (C: 1.46 s, D: 1.46 s) and about 0.05 s without congestion (A). The removal is
seen within about 1 to 1.2 s after the node recovers under congestion (0.4 s in A). The longer installation time under congestion includes the
first critical report's own delay, the server's alert and the HTTP request. These timings are approximate because
of the one-second snapshot interval.

## Limitations

- Only 3 trials per condition, one critical agent (agent2) at a time in A to D, and 10 to 13 critical reports per
  trial, so the averages are based on few samples. The SD is a sample SD of three numbers.
- One switch and one bottleneck port (`s1-eth4`, 5 Mbps in the experiments; the manual demo uses 10 Mbps). The
  controller assumes the server is on switch port 4.
- Agents and server are simulated hosts on one VM, so absolute delays depend on the VM's load. Results show the
  relative effect of prioritisation, not real-network numbers.
- Reports use UDP, so delivery is not guaranteed. The rule lease (25 s) removes stale rules if a RECOVERED alert
  is lost, but there is no controller failover.
- The experiments ran on the server version that was merged at the time (commit 874f658). Later server work
  (duplicate handling, CSV logs, alert retries) was not part of these runs.
- Critical to warning to normal sends RECOVERED twice; the controller treats the repeat as harmless.

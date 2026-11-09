#!/bin/bash
# Builds a Mininet command file for one trial (no nested "source").
# usage: bash experiments/make_trial.sh <scenario.mn> <capture_name> [on|off] [wait_seconds] > experiments/trial.mn
SCEN=$1
CAP=$2
LOAD=${3:-on}
WAIT=${4:-45}
cat << EOF
server pkill -TERM -f '^python3 -u d1_server/monitor_server.py'
sh pkill -f iperf
sh pkill -f tcpdump
sh pkill -f agent.py
sh rm -f results/agent_logs/*
sh bash integration/setup_queues.sh s1-eth4 5000000
server python3 -u d1_server/monitor_server.py -c d1_server/mininet_config.json --no-dashboard > /tmp/cn-integration/server.log 2>&1 &
server iperf -s -u -p 6000 > /tmp/cn-integration/iperf_server.txt 2>&1 &
server tcpdump -i server-eth0 -n -tt -l -A -s 0 "udp and (dst port 5000 or dst port 5001)" > /tmp/cn-integration/$CAP.txt 2>/dev/null &
sh sleep 2
EOF
if [ "$LOAD" = "on" ]; then
  echo 'agent1 iperf -c 10.0.0.4 -u -p 6000 -b 15M -l 1400 -P 3 -t 60 > /tmp/cn-integration/iperf_client.txt 2>&1 &'
  echo 'sh sleep 5'
fi
grep '^agent[0-9]' "$SCEN"
echo "sh sleep $WAIT"
cat << EOF
sh pkill -f tcpdump
sh pkill -f "iperf -c"
server pkill -TERM -f '^python3 -u d1_server/monitor_server.py'
sh sleep 2
EOF

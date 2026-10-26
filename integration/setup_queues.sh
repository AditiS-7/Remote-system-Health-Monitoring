#!/bin/bash
# Creates two queues on the switch port that faces the server.
#   queue 0 = normal traffic, queue 1 = high-priority (critical) traffic
# Run as root while the Mininet topology is running.
PORT=${1:-s1-eth4}
LINK=10000000   # 10 Mbps link capacity for the test

# remove old QoS settings first so reruns start clean
ovs-vsctl clear port "$PORT" qos 2>/dev/null
ovs-vsctl --all destroy qos 2>/dev/null
ovs-vsctl --all destroy queue 2>/dev/null

ovs-vsctl -- set port "$PORT" qos=@newqos \
  -- --id=@newqos create qos type=linux-htb other-config:max-rate=$LINK queues:0=@q0 queues:1=@q1 \
  -- --id=@q0 create queue other-config:min-rate=1000000 other-config:max-rate=$LINK other-config:priority=1 \
  -- --id=@q1 create queue other-config:min-rate=8000000 other-config:max-rate=$LINK other-config:priority=0

echo "Queues created on $PORT"

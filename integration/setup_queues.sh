#!/bin/bash
ovs-vsctl -- set port s1-eth4 qos=@newqos \
  -- --id=@newqos create qos type=linux-htb other-config:max-rate=10000000 queues:0=@q0 queues:1=@q1 \
  -- --id=@q0 create queue other-config:min-rate=1000000 other-config:max-rate=10000000 other-config:priority=1 \
  -- --id=@q1 create queue other-config:min-rate=8000000 other-config:max-rate=10000000 other-config:priority=0
echo "Queues created on s1-eth4"#!/bin/bash
# Gives Mininet hosts a path to the Ryu REST API (port 8080) running on the VM.
# Run AFTER topology.py has started. Safe to re-run.
ip link show veth-ctl >/dev/null 2>&1 && ip link del veth-ctl
ovs-vsctl --if-exists del-port s1 veth-sw

ip link add veth-ctl type veth peer name veth-sw
ovs-vsctl add-port s1 veth-sw
ip addr add 10.0.0.254/24 dev veth-ctl
ip link set veth-ctl up
ip link set veth-sw up
echo "Controller link ready: VM is reachable at 10.0.0.254"

#!/usr/bin/env bash
# Changes only the requested Mininet port, never every QoS/Queue in OVSDB.
set -euo pipefail
PORT=${1:-s1-eth4}
LINK=${2:-10000000}
[[ $EUID -eq 0 ]] || { echo "Run as root after topology.py starts" >&2; exit 1; }
[[ $LINK =~ ^[0-9]+$ ]] && (( LINK >= 10000 )) || {
  echo "Rate must be an integer >= 10000 bits/s" >&2; exit 1;
}
ovs-vsctl get Port "$PORT" name >/dev/null
NORMAL_MIN=$((LINK / 10))
CRITICAL_MIN=$((LINK * 8 / 10))
OLD_QOS=$(ovs-vsctl --if-exists get Port "$PORT" qos)
OLD_QUEUES=()
if [[ $OLD_QOS != '[]' && -n $OLD_QOS ]]; then
  mapfile -t OLD_QUEUES < <(ovs-vsctl get QoS "$OLD_QOS" queues | grep -oE '[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}' || true)
fi
ovs-vsctl -- set Port "$PORT" qos=@newqos \
  -- --id=@newqos create QoS type=linux-htb other-config:max-rate="$LINK" queues:0=@q0 queues:1=@q1 \
  -- --id=@q0 create Queue other-config:min-rate="$NORMAL_MIN" other-config:max-rate="$LINK" other-config:priority=1 \
  -- --id=@q1 create Queue other-config:min-rate="$CRITICAL_MIN" other-config:max-rate="$LINK" other-config:priority=0
# Delete detached old records only if they are no longer referenced anywhere.
if [[ $OLD_QOS != '[]' && -n $OLD_QOS ]]; then
  REFS=$(ovs-vsctl --columns=_uuid --format=csv --data=bare --no-headings find Port qos="$OLD_QOS")
  if [[ -z $REFS ]]; then
    ovs-vsctl --if-exists destroy QoS "$OLD_QOS"
    for queue in "${OLD_QUEUES[@]}"; do
      if ! ovs-vsctl --columns=queues --data=bare --no-headings list QoS | grep -Fq "$queue"; then
        ovs-vsctl --if-exists destroy Queue "$queue"
      fi
    done
  fi
fi
echo "Queues on $PORT: max=$LINK bit/s, queue0 min=$NORMAL_MIN, queue1 min=$CRITICAL_MIN"
echo "Controller REST uses topology ctl at 10.0.0.100:8080; no extra link needed."

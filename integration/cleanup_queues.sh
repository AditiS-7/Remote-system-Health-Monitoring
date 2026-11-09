#!/usr/bin/env bash
set -euo pipefail

PORT="${1:-s1-eth4}"

if [[ "$EUID" -ne 0 ]]; then
    echo "Run with sudo."
    exit 1
fi

QOS_IDS="$(ovs-vsctl --bare --no-heading --columns=_uuid \
    find QoS external_ids:cn_d2_port="$PORT")"

QUEUE_IDS="$(ovs-vsctl --bare --no-heading --columns=_uuid \
    find Queue external_ids:cn_d2_port="$PORT")"

ATTACHED="$(ovs-vsctl --if-exists get Port "$PORT" qos)"
ATTACHED="${ATTACHED//[\[\]\" ]/}"

for id in $QOS_IDS; do
    if [[ "$ATTACHED" == "$id" ]]; then
        ovs-vsctl --if-exists clear Port "$PORT" qos
    fi
    ovs-vsctl --if-exists destroy QoS "$id"
done

for id in $QUEUE_IDS; do
    ovs-vsctl --if-exists destroy Queue "$id"
done

echo "Removed tagged D2 queue configuration for $PORT"

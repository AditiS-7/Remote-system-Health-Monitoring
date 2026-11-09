#!/usr/bin/env bash
set -euo pipefail

PORT="${1:-s1-eth4}"
RATE="${2:-10000000}"

if [[ "$EUID" -ne 0 ]]; then
    echo "Run with sudo."
    exit 1
fi

if ! [[ "$RATE" =~ ^[0-9]+$ ]]; then
    echo "Rate must be an integer in bits/sec."
    exit 1
fi

if (( RATE < 1000000 )); then
    echo "Rate must be at least 1000000 bits/sec."
    exit 1
fi

ovs-vsctl get Port "$PORT" name >/dev/null

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
bash "$SCRIPT_DIR/cleanup_queues.sh" "$PORT"

NORMAL_MIN=$((RATE / 10))
CRITICAL_MIN=$((RATE * 8 / 10))

ovs-vsctl \
    -- set Port "$PORT" qos=@qos \
    -- --id=@qos create QoS \
        type=linux-htb \
        external_ids:cn_d2_port="$PORT" \
        other-config:max-rate="$RATE" \
        queues:0=@normal \
        queues:1=@critical \
    -- --id=@normal create Queue \
        external_ids:cn_d2_port="$PORT" \
        other-config:min-rate="$NORMAL_MIN" \
        other-config:max-rate="$RATE" \
        other-config:priority=1 \
    -- --id=@critical create Queue \
        external_ids:cn_d2_port="$PORT" \
        other-config:min-rate="$CRITICAL_MIN" \
        other-config:max-rate="$RATE" \
        other-config:priority=0

echo "Configured $PORT: total rate $RATE bits/sec"
echo "Queue 0: normal traffic, minimum $NORMAL_MIN bits/sec"
echo "Queue 1: critical traffic, minimum $CRITICAL_MIN bits/sec"

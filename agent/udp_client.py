import json
import os
import socket
import argparse


def load_config():
    """Read the shared config.json from the repo root."""
    path = os.path.join(os.path.dirname(__file__), "..", "config.json")
    with open(path) as f:
        return json.load(f)


def create_socket():
    """Create a UDP socket (SOCK_DGRAM = UDP)."""
    return socket.socket(socket.AF_INET, socket.SOCK_DGRAM)


def send_message(sock, message, ip, port):
    """Turn a dict into JSON bytes and send it as one UDP datagram."""
    data = json.dumps(message).encode("utf-8")
    sock.sendto(data, (ip, port))


if __name__ == "__main__":
    cfg = load_config()

    parser = argparse.ArgumentParser()
    parser.add_argument("--server", default=cfg["server_ip"])
    parser.add_argument("--port", type=int, default=cfg["normal_port"])
    args = parser.parse_args()

    sock = create_socket()
    send_message(sock, {"node_id": "agent1", "test": "hello"}, args.server, args.port)
    print(f"Sent test message to {args.server}:{args.port}")
    sock.close()

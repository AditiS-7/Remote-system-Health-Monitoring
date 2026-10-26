import select
import socket

socks = {}
for port in (5000, 5001):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("0.0.0.0", port))
    socks[s] = port

print("Listening on UDP 5000 (normal) and 5001 (critical)...")
while True:
    ready, _, _ = select.select(list(socks), [], [])
    for s in ready:
        data, addr = s.recvfrom(1024)
        tag = "CRITICAL" if socks[s] == 5001 else "normal"
        print(f"[{tag}] {addr}: {data.decode()}")

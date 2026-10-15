import json
import selectors
import socket

selector = selectors.DefaultSelector()

for port in (5000, 5001):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("10.0.0.4", port))
    selector.register(sock, selectors.EVENT_READ)

print("Listening on 10.0.0.4, UDP ports 5000 and 5001", flush=True)

try:
    while True:
        for key, _ in selector.select():
            sock = key.fileobj
            data, source = sock.recvfrom(65535)
            try:
                report = json.loads(data)
                print(
                    f"source={source[0]} port={sock.getsockname()[1]} "
                    f"report={report}",
                    flush=True
                )
            except (ValueError, UnicodeDecodeError):
                print(f"Invalid JSON from {source}", flush=True)
except KeyboardInterrupt:
    pass
finally:
    for key in list(selector.get_map().values()):
        key.fileobj.close()
    selector.close()

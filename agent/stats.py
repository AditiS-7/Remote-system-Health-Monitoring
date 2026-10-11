import psutil


def collect_stats():
    """Return the current health numbers of this machine."""
    net = psutil.net_io_counters()
    return {
        "cpu": psutil.cpu_percent(interval=None),   # % of CPU used
        "mem": psutil.virtual_memory().percent,     # % of RAM used
        "procs": len(psutil.pids()),                # number of running processes
        "net_sent": net.bytes_sent,                 # total bytes sent so far
        "net_recv": net.bytes_recv,                 # total bytes received so far
    }


if __name__ == "__main__":
    import time
    psutil.cpu_percent(interval=None)   # first call always returns 0.0, so "warm it up"
    time.sleep(1)
    for _ in range(5):
        print(collect_stats())
        time.sleep(2)

from mininet.net import Mininet
from mininet.node import RemoteController, OVSSwitch
from mininet.cli import CLI
from mininet.log import setLogLevel


def main():
    net = Mininet(
        controller=None,
        switch=OVSSwitch,
        autoSetMacs=True
    )

    net.addController(
        "c0",
        controller=RemoteController,
        ip="127.0.0.1",
        port=6653
    )

    switch = net.addSwitch(
        "s1",
        protocols="OpenFlow13",
        failMode="secure"
    )

    for name, ip in [
        ("agent1", "10.0.0.1/24"),
        ("agent2", "10.0.0.2/24"),
        ("agent3", "10.0.0.3/24"),
        ("server", "10.0.0.4/24"),
    ]:
        host = net.addHost(name, ip=ip)
        net.addLink(host, switch)

    try:
        net.start()
        CLI(net)
    finally:
        net.stop()


if __name__ == "__main__":
    setLogLevel("info")
    main()

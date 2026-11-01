from mininet.net import Mininet
from mininet.node import RemoteController, OVSSwitch, Node
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

    for name, ip, switch_port in [
        ("agent1", "10.0.0.1/24", 1),
        ("agent2", "10.0.0.2/24", 2),
        ("agent3", "10.0.0.3/24", 3),
        ("server", "10.0.0.4/24", 4),
    ]:
        host = net.addHost(name, ip=ip)
        net.addLink(host, switch, port2=switch_port)

    # Root-namespace interface for reaching Ryu's REST API.
    ctl = Node("ctl", inNamespace=False)
    ctl_link = net.addLink(ctl, switch, port2=5)
    ctl.setIP("10.0.0.100/24", intf=ctl_link.intf1)

    try:
        net.start()
        CLI(net)
    finally:
        net.stop()


if __name__ == "__main__":
    setLogLevel("info")
    main()

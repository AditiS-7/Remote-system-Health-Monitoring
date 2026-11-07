"""
health_controller.py  (Person 4: Socket-SDN integration)

One Ryu app that does two jobs:

  1. Basic learning-switch forwarding (OpenFlow 1.3), so hosts can talk.
  2. Listens for alerts from the monitoring server (HTTP POST /alert).
       CRITICAL  -> install a high-priority flow rule for that node's critical
                    UDP traffic (to the server, port 5001) that uses queue 1.
       RECOVERED -> remove that rule again.

Other endpoint:  GET /status  shows which nodes are currently prioritized.

Run (Ryu virtualenv active, no sudo):
    ryu-manager integration/health_controller.py
"""
import json
import ipaddress
import math
import os
import time

from webob import Response

from ryu.app.wsgi import ControllerBase, WSGIApplication, route
from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, DEAD_DISPATCHER, set_ev_cls
from ryu.lib import hub
from ryu.lib.packet import ether_types, ethernet, packet
from ryu.ofproto import ofproto_v1_3

APP_NAME = "health_controller_app"

# Values that match config.json and topology.py
SERVER_IP = "10.0.0.4"
SERVER_PORT_NO = 4          # switch port facing the server (s1-eth4)
CRITICAL_UDP_PORT = 5001    # agents send critical reports here
PRIORITY_QUEUE = 1          # queue 1 = high priority (see setup_queues.sh)
PRIORITY_RULE = 100         # priority of the dynamic rule (learning rules use 1)
PRIORITY_COOKIE = 0x434E0001
AGENT_IPS = {"10.0.0.1", "10.0.0.2", "10.0.0.3"}


class HealthController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]
    _CONTEXTS = {"wsgi": WSGIApplication}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.mac_to_port = {}
        self.datapaths = {}
        self.prioritized = {}   # src_ip -> {"node_id", "since", "reasons"}
        self.mode = os.environ.get("HEALTH_MODE", "dynamic").lower()
        self.lease_seconds = int(os.environ.get("HEALTH_LEASE_SECONDS", "25"))
        if self.mode not in ("static", "dynamic"):
            raise ValueError("HEALTH_MODE must be static or dynamic")
        if not 12 <= self.lease_seconds <= 65535:
            raise ValueError("HEALTH_LEASE_SECONDS must be between 12 and 65535")
        kwargs["wsgi"].register(AlertAPI, {APP_NAME: self})
        self._lease_thread = hub.spawn(self.lease_loop)
        self.logger.info("health mode=%s lease=%ss", self.mode, self.lease_seconds)

    # ---------------------------------------------------------------
    # Part 1: learning switch
    # ---------------------------------------------------------------
    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        dp = ev.msg.datapath
        ofp, parser = dp.ofproto, dp.ofproto_parser
        self.datapaths[dp.id] = dp
        self.mac_to_port.pop(dp.id, None)
        # Clear only this app's D2 cookie and the three known D1 priority rules.
        dp.send_msg(parser.OFPFlowMod(
            datapath=dp, command=ofp.OFPFC_DELETE, cookie=PRIORITY_COOKIE,
            cookie_mask=0xffffffffffffffff, out_port=ofp.OFPP_ANY,
            out_group=ofp.OFPG_ANY, match=parser.OFPMatch()))
        for src_ip in AGENT_IPS:
            self.delete_priority(dp, src_ip)
        # table-miss: send unknown packets to the controller
        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofp.OFPP_CONTROLLER, ofp.OFPCML_NO_BUFFER)]
        self.add_flow(dp, 0, match, actions)
        now = time.time()
        for src_ip, state in list(self.prioritized.items()):
            remaining = state["expires_at"] - now
            if remaining > 0 and self.mode == "dynamic":
                self.install_priority(dp, src_ip, math.ceil(remaining))
        self.logger.info("switch %s connected", dp.id)

    @set_ev_cls(ofp_event.EventOFPStateChange, DEAD_DISPATCHER)
    def switch_disconnected(self, ev):
        if self.datapaths.get(ev.datapath.id) is ev.datapath:
            self.datapaths.pop(ev.datapath.id, None)
            self.mac_to_port.pop(ev.datapath.id, None)

    def add_flow(self, dp, priority, match, actions, hard_timeout=0, cookie=0):
        ofp, parser = dp.ofproto, dp.ofproto_parser
        inst = [parser.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS, actions)]
        dp.send_msg(parser.OFPFlowMod(datapath=dp, priority=priority,
                                      match=match, instructions=inst,
                                      hard_timeout=hard_timeout, cookie=cookie))

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev):
        msg = ev.msg
        dp = msg.datapath
        ofp, parser = dp.ofproto, dp.ofproto_parser
        in_port = msg.match["in_port"]

        pkt = packet.Packet(msg.data)
        protocols = pkt.get_protocols(ethernet.ethernet)
        if not protocols:
            return
        eth = protocols[0]
        if eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return

        table = self.mac_to_port.setdefault(dp.id, {})
        table[eth.src] = in_port
        out_port = table.get(eth.dst, ofp.OFPP_FLOOD)
        actions = [parser.OFPActionOutput(out_port)]

        if out_port != ofp.OFPP_FLOOD:
            match = parser.OFPMatch(in_port=in_port, eth_dst=eth.dst, eth_src=eth.src)
            self.add_flow(dp, 1, match, actions)

        data = msg.data if msg.buffer_id == ofp.OFP_NO_BUFFER else None
        dp.send_msg(parser.OFPPacketOut(datapath=dp, buffer_id=msg.buffer_id,
                                        in_port=in_port, actions=actions, data=data))

    # ---------------------------------------------------------------
    # Part 2: alerts from the monitoring server
    # ---------------------------------------------------------------
    @staticmethod
    def critical_match(parser, src_ip):
        """Critical report traffic from one agent to the server."""
        return parser.OFPMatch(eth_type=0x0800, ip_proto=17,
                               ipv4_src=src_ip, ipv4_dst=SERVER_IP,
                               udp_dst=CRITICAL_UDP_PORT)

    def handle_alert(self, alert):
        """Returns (http_status_code, response_dict)."""
        level = str(alert.get("level", "")).upper()
        node = alert.get("node_id", "?")
        src_ip = alert.get("src_ip")
        try:
            src_ip = str(ipaddress.IPv4Address(src_ip))
        except (ipaddress.AddressValueError, TypeError):
            return 400, {"status": "error", "reason": "invalid or missing src_ip"}
        if src_ip not in AGENT_IPS:
            return 400, {"status": "error", "reason": "src_ip must be a configured agent"}
        if self.mode == "static":
            return 200, {"status": "ignored", "mode": "static", "level": level}

        if level == "CRITICAL":
            return self.prioritize(node, src_ip, alert.get("reasons", []))
        if level == "RECOVERED":
            return self.restore(node, src_ip)
        return 200, {"status": "ignored", "level": level}

    def prioritize(self, node, src_ip, reasons):
        if not self.datapaths:
            return 503, {"status": "error", "reason": "no switch connected"}

        for dp in self.datapaths.values():
            self.install_priority(dp, src_ip, self.lease_seconds)

        now = time.time()
        previous = self.prioritized.get(src_ip)
        self.prioritized[src_ip] = {"node_id": node,
                                    "since": previous["since"] if previous else now,
                                    "last_alert": now, "expires_at": now + self.lease_seconds,
                                    "reasons": reasons}
        self.logger.info("CRITICAL %s (%s): priority rule installed, queue %d",
                         node, src_ip, PRIORITY_QUEUE)
        return 200, {"status": "ok", "action": "refreshed" if previous else "prioritized",
                     "node_id": node}

    def install_priority(self, dp, src_ip, seconds):
        parser = dp.ofproto_parser
        actions = [parser.OFPActionSetQueue(PRIORITY_QUEUE),
                   parser.OFPActionOutput(SERVER_PORT_NO)]
        # An identical ADD replaces the rule and renews its hard timeout.
        # Switch-side expiry still works if the controller process crashes.
        self.add_flow(dp, PRIORITY_RULE, self.critical_match(parser, src_ip), actions,
                      hard_timeout=seconds, cookie=PRIORITY_COOKIE)

    def delete_priority(self, dp, src_ip):
        ofp, parser = dp.ofproto, dp.ofproto_parser
        dp.send_msg(parser.OFPFlowMod(
            datapath=dp, command=ofp.OFPFC_DELETE_STRICT,
            out_port=ofp.OFPP_ANY, out_group=ofp.OFPG_ANY,
            priority=PRIORITY_RULE, match=self.critical_match(parser, src_ip)))

    def expire_priorities(self, now=None):
        now = time.time() if now is None else now
        for src_ip, state in list(self.prioritized.items()):
            if now >= state["expires_at"]:
                self.restore(state["node_id"], src_ip)
                self.logger.info("EXPIRED %s: no CRITICAL refresh within lease", src_ip)

    def lease_loop(self):
        while True:
            hub.sleep(1)
            self.expire_priorities()

    def restore(self, node, src_ip):
        if src_ip not in self.prioritized:
            return 200, {"status": "ok", "action": "nothing_to_do", "node_id": node}

        for dp in self.datapaths.values():
            self.delete_priority(dp, src_ip)

        del self.prioritized[src_ip]
        self.logger.info("RECOVERED %s (%s): priority rule removed", node, src_ip)
        return 200, {"status": "ok", "action": "restored", "node_id": node}


class AlertAPI(ControllerBase):
    """HTTP interface used by the monitoring server."""

    def __init__(self, req, link, data, **config):
        super().__init__(req, link, data, **config)
        self.app = data[APP_NAME]

    @route("alert", "/alert", methods=["POST"])
    def post_alert(self, req, **kwargs):
        try:
            alert = json.loads(req.body.decode("utf-8"))
            if not isinstance(alert, dict):
                raise ValueError("alert must be a JSON object")
        except ValueError:
            return self._reply(400, {"status": "error", "reason": "bad JSON"})
        code, body = self.app.handle_alert(alert)
        return self._reply(code, body)

    @route("status", "/status", methods=["GET"])
    def get_status(self, req, **kwargs):
        self.app.expire_priorities()
        return self._reply(200, {"mode": self.app.mode,
                                 "lease_seconds": self.app.lease_seconds,
                                 "switches": sorted(self.app.datapaths),
                                 "prioritized": self.app.prioritized})

    @staticmethod
    def _reply(code, body):
        return Response(status=code, content_type="application/json",
                        body=json.dumps(body).encode("utf-8"))

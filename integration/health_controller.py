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
import time

from webob import Response

from ryu.app.wsgi import ControllerBase, WSGIApplication, route
from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
from ryu.lib.packet import ether_types, ethernet, packet
from ryu.ofproto import ofproto_v1_3

APP_NAME = "health_controller_app"

# Values that match config.json and topology.py
SERVER_IP = "10.0.0.4"
SERVER_PORT_NO = 4          # switch port facing the server (s1-eth4)
CRITICAL_UDP_PORT = 5001    # agents send critical reports here
PRIORITY_QUEUE = 1          # queue 1 = high priority (see setup_queues.sh)
PRIORITY_RULE = 100         # priority of the dynamic rule (learning rules use 1)


class HealthController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]
    _CONTEXTS = {"wsgi": WSGIApplication}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.mac_to_port = {}
        self.datapaths = {}
        self.prioritized = {}   # src_ip -> {"node_id", "since", "reasons"}
        kwargs["wsgi"].register(AlertAPI, {APP_NAME: self})

    # ---------------------------------------------------------------
    # Part 1: learning switch
    # ---------------------------------------------------------------
    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        dp = ev.msg.datapath
        ofp, parser = dp.ofproto, dp.ofproto_parser
        self.datapaths[dp.id] = dp
        # table-miss: send unknown packets to the controller
        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofp.OFPP_CONTROLLER, ofp.OFPCML_NO_BUFFER)]
        self.add_flow(dp, 0, match, actions)
        self.logger.info("switch %s connected", dp.id)

    def add_flow(self, dp, priority, match, actions):
        ofp, parser = dp.ofproto, dp.ofproto_parser
        inst = [parser.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS, actions)]
        dp.send_msg(parser.OFPFlowMod(datapath=dp, priority=priority,
                                      match=match, instructions=inst))

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev):
        msg = ev.msg
        dp = msg.datapath
        ofp, parser = dp.ofproto, dp.ofproto_parser
        in_port = msg.match["in_port"]

        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocols(ethernet.ethernet)[0]
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
        if not src_ip:
            return 400, {"status": "error", "reason": "missing src_ip"}

        if level == "CRITICAL":
            return self.prioritize(node, src_ip, alert.get("reasons", []))
        if level == "RECOVERED":
            return self.restore(node, src_ip)
        return 200, {"status": "ignored", "level": level}

    def prioritize(self, node, src_ip, reasons):
        if src_ip in self.prioritized:
            return 200, {"status": "ok", "action": "already_prioritized", "node_id": node}
        if not self.datapaths:
            return 503, {"status": "error", "reason": "no switch connected"}

        for dp in self.datapaths.values():
            ofp, parser = dp.ofproto, dp.ofproto_parser
            actions = [parser.OFPActionSetQueue(PRIORITY_QUEUE),
                       parser.OFPActionOutput(SERVER_PORT_NO)]
            self.add_flow(dp, PRIORITY_RULE, self.critical_match(parser, src_ip), actions)

        self.prioritized[src_ip] = {"node_id": node, "since": time.time(),
                                    "reasons": reasons}
        self.logger.info("CRITICAL %s (%s): priority rule installed, queue %d",
                         node, src_ip, PRIORITY_QUEUE)
        return 200, {"status": "ok", "action": "prioritized", "node_id": node}

    def restore(self, node, src_ip):
        if src_ip not in self.prioritized:
            return 200, {"status": "ok", "action": "nothing_to_do", "node_id": node}

        for dp in self.datapaths.values():
            ofp, parser = dp.ofproto, dp.ofproto_parser
            dp.send_msg(parser.OFPFlowMod(
                datapath=dp, command=ofp.OFPFC_DELETE_STRICT,
                out_port=ofp.OFPP_ANY, out_group=ofp.OFPG_ANY,
                priority=PRIORITY_RULE,
                match=self.critical_match(parser, src_ip)))

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
        return self._reply(200, {"prioritized": self.app.prioritized})

    @staticmethod
    def _reply(code, body):
        return Response(status=code, content_type="application/json",
                        body=json.dumps(body).encode("utf-8"))

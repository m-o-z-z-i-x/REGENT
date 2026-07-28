import pytest

from regent.topology import (
  findUplinkName, findGateway, networksOf, dhcpEnabledFor,
  collectAccessPoints, findUplinkSsid, analyse
)

REAL_ROUTE = "default via 192.168.1.1 dev phy1-sta0 proto static src 192.168.1.6\n192.168.10.0/24 dev br-lan proto kernel scope link src 192.168.10.1"

# ---------------------------------------------------------------------------- #
# a real broken router: lan and uplink on one subnet, the AP on a dead interface, dhcp off
# ---------------------------------------------------------------------------- #
BROKEN = {
  "interfaces": [
    {"name": "lan", "up": True, "proto": "static", "device": "br-lan", "addresses": ["192.168.1.1/24"], "uptime": 11814},
    {"name": "wwan", "up": True, "proto": "dhcp", "device": "phy1-sta0", "addresses": ["192.168.1.4/24"], "uptime": 11800},
    {"name": "wwan2", "up": False, "proto": "dhcp", "device": None, "addresses": [], "uptime": 0}
  ],
  "radios": [
    {"radio": "radio1", "band": "2g", "channel": "auto", "interfaces": [
      {"ifname": "phy1-sta0", "mode": "sta", "ssid": "UpstreamAP", "networks": ["wwan"]},
      {"ifname": "phy1-ap0", "mode": "ap", "ssid": "HomeNet", "networks": ["wwan2"]}
    ]}
  ],
  "zones": [
    {"name": "lan", "networks": ["lan", "wwan"], "masquerade": False},
    {"name": "wwan", "networks": ["wwan2"], "masquerade": True}
  ],
  "forwardings": [{"src": "lan", "dest": "wwan"}],
  "dhcp": {"lan": {".type": "dhcp", "interface": "lan", "ignore": "1"}},
  "route": "default via 192.168.1.1 dev phy1-sta0 proto static src 192.168.1.4"
}

# ---------------------------------------------------------------------------- #
# the same router after the fix
# ---------------------------------------------------------------------------- #
FIXED = {
  "interfaces": [
    {"name": "lan", "up": True, "proto": "static", "device": "br-lan", "addresses": ["192.168.10.1/24"], "uptime": 2872},
    {"name": "loopback", "up": True, "proto": "static", "device": "lo", "addresses": ["127.0.0.1/8"], "uptime": 2872},
    {"name": "wwan", "up": True, "proto": "dhcp", "device": "phy1-sta0", "addresses": ["192.168.1.6/24"], "uptime": 2798}
  ],
  "radios": [
    {"radio": "radio0", "band": "5g", "channel": "36", "interfaces": [
      {"ifname": "phy0-ap0", "mode": "ap", "ssid": "HomeNet", "networks": ["lan"]}
    ]},
    {"radio": "radio1", "band": "2g", "channel": "auto", "interfaces": [
      {"ifname": "phy1-sta0", "mode": "sta", "ssid": "UpstreamAP", "networks": ["wwan"]}
    ]}
  ],
  "zones": [
    {"name": "lan", "networks": ["lan"], "masquerade": False},
    {"name": "wan", "networks": [], "masquerade": False},
    {"name": "wwan", "networks": ["wwan"], "masquerade": True}
  ],
  "forwardings": [{"src": "lan", "dest": "wwan"}],
  "dhcp": {"lan": {".type": "dhcp", "interface": "lan", "start": "100"}},
  "route": REAL_ROUTE
}

def run(fixture, stationCounts = None):
  return analyse(
    fixture["interfaces"], fixture["radios"], stationCounts or {},
    fixture["zones"], fixture["forwardings"],
    fixture["dhcp"], fixture["route"], clientCount = 3
  )

def test_findUplinkNameReadsTheDefaultRoute():
  assert findUplinkName(REAL_ROUTE) == "phy1-sta0"

def test_findGatewayReadsTheDefaultRoute():
  assert findGateway(REAL_ROUTE) == "192.168.1.1"

def test_findUplinkNameReturnsNothingWithoutADefaultRoute():
  assert findUplinkName("192.168.10.0/24 dev br-lan scope link") is None

def test_networksOfCollapsesAddressesToTheirSubnet():
  assert str(networksOf(["192.168.10.1/24"])[0]) == "192.168.10.0/24"

def test_networksOfSkipsGarbage():
  assert networksOf(["not-an-address"]) == []

def test_dhcpEnabledIsFalseWhenIgnoreIsSet():
  assert dhcpEnabledFor(BROKEN["dhcp"], "lan") is False

def test_dhcpEnabledIsTrueWithoutIgnore():
  assert dhcpEnabledFor(FIXED["dhcp"], "lan") is True

def test_dhcpEnabledIsFalseForAnInterfaceWithNoSection():
  assert dhcpEnabledFor(FIXED["dhcp"], "guest") is False

def test_collectAccessPointsIgnoresTheClientInterface():
  points = collectAccessPoints(FIXED["radios"], {"phy0-ap0": 1})

  assert len(points) == 1
  assert points[0]["ssid"] == "HomeNet"
  assert points[0]["band"] == "5g"
  assert points[0]["stations"] == 1

def test_findUplinkSsidNamesTheNetworkWeJoined():
  assert findUplinkSsid(FIXED["radios"], "phy1-sta0") == "UpstreamAP"

def test_theFixedRouterReportsNoWarnings():
  assert run(FIXED)["warnings"] == []

def test_theFixedRouterDescribesItsUplink():
  uplink = run(FIXED)["uplink"]

  assert uplink["interface"] == "wwan"
  assert uplink["ssid"] == "UpstreamAP"
  assert uplink["gateway"] == "192.168.1.1"
  assert uplink["addresses"] == ["192.168.1.6/24"]

def test_theFixedRouterListsItsAccessPointAndLan():
  report = run(FIXED)

  assert [lan["interface"] for lan in report["lans"]] == ["lan"]
  assert report["accessPoints"][0]["ssid"] == "HomeNet"
  assert report["masqueradedZones"] == ["wwan"]
  assert report["forwardings"] == ["lan -> wwan"]

def test_theBrokenRouterIsCaughtOnEveryCount():
  warnings = " | ".join(run(BROKEN)["warnings"])

  assert "same subnet as the uplink" in warnings
  assert "not up" in warnings
  assert "dhcp is not serving" in warnings
  assert "does not masquerade" in warnings

def test_theSubnetCollisionExplainsItself():
  # the message has to say why it matters, not merely that two subnets match
  collision = next(w for w in run(BROKEN)["warnings"] if "same subnet" in w)

  assert "192.168.1.0/24" in collision
  assert "resolves to this router itself" in collision

def test_anAccessPointOnADeadNetworkIsNamed():
  warning = next(w for w in run(BROKEN)["warnings"] if "not up" in w)

  assert "HomeNet" in warning
  assert "wwan2" in warning

def test_aRouterWithNoDefaultRouteIsFlagged():
  fixture = dict(FIXED, route = "192.168.10.0/24 dev br-lan scope link")
  report = analyse(
    fixture["interfaces"], fixture["radios"], {},
    fixture["zones"], fixture["forwardings"],
    fixture["dhcp"], fixture["route"], clientCount = 0
  )

  assert report["uplink"] is None
  assert any("no default route" in w for w in report["warnings"])
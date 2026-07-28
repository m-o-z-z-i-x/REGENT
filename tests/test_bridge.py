import pytest

from regent.bridge import (
  subnetOf, pickFreeSubnet, findZoneFor, hasForwarding, findApInterfaces,
  dhcpSectionFor, planShareUplink, planJoinUpstream, chooseUplinkZone, BridgeError, CANDIDATE_SUBNETS
)

# ---------------------------------------------------------------------------- #
# a real broken router: lan and uplink on one subnet, the AP on a dead interface, dhcp off
# ---------------------------------------------------------------------------- #
BROKEN = dict(
  interfaces = [
    {"name": "lan", "up": True, "proto": "static", "device": "br-lan", "addresses": ["192.168.1.1/24"], "uptime": 11814},
    {"name": "wwan", "up": True, "proto": "dhcp", "device": "phy1-sta0", "addresses": ["192.168.1.4/24"], "uptime": 11800},
    {"name": "wwan2", "up": False, "proto": "dhcp", "device": None, "addresses": [], "uptime": 0},
  ],
  radios = [
    {"radio": "radio1", "band": "2g", "channel": "auto", "interfaces": [
      {"section": "wifinet0", "ifname": "phy1-sta0", "mode": "sta", "ssid": "UpstreamAP", "networks": ["wwan"]},
      {"section": "wifinet1", "ifname": "phy1-ap0", "mode": "ap", "ssid": "HomeNet", "networks": ["wwan2"]},
    ]},
  ],
  zones = [
    {"section": "@zone[0]", "name": "lan", "networks": ["lan", "wwan"], "masquerade": False},
    {"section": "@zone[1]", "name": "wwan", "networks": ["wwan2"], "masquerade": True},
  ],
  forwardings = [{"section": "@forwarding[0]", "src": "lan", "dest": "wwan"}],
  dhcpSections = {"lan": {".type": "dhcp", "interface": "lan", "ignore": "1"}},
  routeOutput = "default via 192.168.1.1 dev phy1-sta0 proto static src 192.168.1.4",
)

# ---------------------------------------------------------------------------- #
# the same router after the repair
# ---------------------------------------------------------------------------- #
FIXED = dict(
  interfaces = [
    {"name": "lan", "up": True, "proto": "static", "device": "br-lan", "addresses": ["192.168.10.1/24"], "uptime": 2872},
    {"name": "wwan", "up": True, "proto": "dhcp", "device": "phy1-sta0", "addresses": ["192.168.1.6/24"], "uptime": 2798},
  ],
  radios = [
    {"radio": "radio0", "band": "5g", "channel": "36", "interfaces": [
      {"section": "wifinet1", "ifname": "phy0-ap0", "mode": "ap", "ssid": "HomeNet", "networks": ["lan"]},
    ]},
    {"radio": "radio1", "band": "2g", "channel": "auto", "interfaces": [
      {"section": "wifinet0", "ifname": "phy1-sta0", "mode": "sta", "ssid": "UpstreamAP", "networks": ["wwan"]},
    ]},
  ],
  zones = [
    {"section": "@zone[0]", "name": "lan", "networks": ["lan"], "masquerade": False},
    {"section": "@zone[1]", "name": "wwan", "networks": ["wwan"], "masquerade": True},
  ],
  forwardings = [{"section": "@forwarding[0]", "src": "lan", "dest": "wwan"}],
  dhcpSections = {"lan": {".type": "dhcp", "interface": "lan", "start": "100"}},
  routeOutput = "default via 192.168.1.1 dev phy1-sta0 proto static src 192.168.1.6",
)

def test_subnetOfCollapsesAnAddress():
  assert str(subnetOf("192.168.10.1")) == "192.168.10.0/24"

def test_pickFreeSubnetAvoidsTheUplink():
  uplink = [subnetOf("192.168.1.4")]

  assert pickFreeSubnet(uplink) == CANDIDATE_SUBNETS[0]

def test_pickFreeSubnetSkipsAColonisedCandidate():
  uplink = [subnetOf("192.168.1.4"), subnetOf("192.168.10.5")]

  assert pickFreeSubnet(uplink) == "192.168.20.1"

def test_pickFreeSubnetGivesUpRatherThanCollide():
  taken = [subnetOf(candidate) for candidate in CANDIDATE_SUBNETS]

  with pytest.raises(BridgeError):
    pickFreeSubnet(taken)

def test_findZoneForLocatesTheOwningZone():
  assert findZoneFor(BROKEN["zones"], "wwan2")["name"] == "wwan"

def test_hasForwardingSpotsAnExistingPair():
  assert hasForwarding(FIXED["forwardings"], "lan", "wwan") is True
  assert hasForwarding(FIXED["forwardings"], "lan", "wan") is False

def test_findApInterfacesIgnoresTheClientRadio():
  points = findApInterfaces(BROKEN["radios"])

  assert len(points) == 1
  assert points[0][1]["ssid"] == "HomeNet"

def test_dhcpSectionForFindsTheServingSection():
  name, section = dhcpSectionFor(BROKEN["dhcpSections"], "lan")

  assert name == "lan"
  assert section["ignore"] == "1"

def test_aCorrectRouterNeedsNoChanges():
  plan = planShareUplink(**FIXED)

  assert plan["alreadyCorrect"] is True
  assert plan["changes"] == []

def test_theBrokenRouterIsFixedInOnePlan():
  changes = " | ".join(planShareUplink(**BROKEN)["changes"])

  assert "network.lan.ipaddr" in changes
  assert "wireless.wifinet1.network='lan'" in changes
  assert "dhcp.lan.ignore" in changes
  assert "firewall.@zone[1].masq" not in changes  # already masquerading

def test_theCollisionIsFixedFirst():
  # everything else is pointless while the gateway resolves to the router itself
  changes = planShareUplink(**BROKEN)["changes"]

  assert "ipaddr" in changes[0]

def test_theNewSubnetAvoidsTheUplink():
  changes = planShareUplink(**BROKEN)["changes"]

  assert "192.168.1." not in changes[0]
  assert "192.168.10.1" in changes[0]

def test_movingTheRouterWarnsAboutStaticClients():
  # a machine with a static address loses the router when the subnet changes under it
  warnings = " ".join(planShareUplink(**BROKEN)["warnings"])

  assert "static" in warnings
  assert "by hand" in warnings

def test_everyChangeCarriesAReason():
  plan = planShareUplink(**BROKEN)

  assert len(plan["reasons"]) >= 3
  assert all(len(reason) > 20 for reason in plan["reasons"])

def test_theUplinkIsRemovedFromTheLanZone():
  changes = " | ".join(planShareUplink(**BROKEN)["changes"])

  assert "firewall.@zone[0].network='lan'" in changes

def test_theUplinkAndLanZonesAreNeverTheSameOne():
  # when the uplink sits in the lan zone, treating that as the uplink zone plans lan -> lan
  state = dict(BROKEN, forwardings = [])
  changes = " | ".join(planShareUplink(**state)["changes"])

  assert "dest='lan'" not in changes
  assert "dest='wwan'" in changes

def test_theUplinkIsMovedIntoTheOutsideZone():
  changes = " | ".join(planShareUplink(**BROKEN)["changes"])

  assert "firewall.@zone[1].network='wwan'" in changes

def test_deadInterfacesAreDroppedFromTheZone():
  # a zone pointing at a deleted interface looks configured but its rules apply to nothing
  plan = planShareUplink(**BROKEN)
  changes = " | ".join(plan["changes"])

  assert "wwan2" not in changes
  assert any("no longer exist" in reason for reason in plan["reasons"])

def test_chooseUplinkZoneNeverReturnsTheLanZone():
  lanZone = BROKEN["zones"][0]
  chosen = chooseUplinkZone(BROKEN["zones"], "wwan", lanZone)

  assert chosen["section"] != lanZone["section"]

def test_chooseUplinkZonePrefersOneAlreadyHoldingTheUplink():
  zones = [
    {"section": "@zone[0]", "name": "lan", "networks": ["lan"], "masquerade": False},
    {"section": "@zone[1]", "name": "guest", "networks": ["wwan"], "masquerade": False},
    {"section": "@zone[2]", "name": "wan", "networks": [], "masquerade": True},
  ]

  assert chooseUplinkZone(zones, "wwan", zones[0])["name"] == "guest"

def test_chooseUplinkZoneFallsBackToAConventionalName():
  zones = [
    {"section": "@zone[0]", "name": "lan", "networks": ["lan"], "masquerade": False},
    {"section": "@zone[1]", "name": "guest", "networks": [], "masquerade": False},
    {"section": "@zone[2]", "name": "wan", "networks": [], "masquerade": True},
  ]

  assert chooseUplinkZone(zones, "wwan", zones[0])["name"] == "wan"

def test_planningRefusesWhenThereIsOnlyOneZone():
  zones = [{"section": "@zone[0]", "name": "lan", "networks": ["lan", "wwan"], "masquerade": False}]

  with pytest.raises(BridgeError) as err:
    planShareUplink(**dict(BROKEN, zones = zones))

  assert "wan zone" in str(err.value)

def test_aMissingDhcpSectionIsCreatedRatherThanEdited():
  state = dict(BROKEN, dhcpSections = {})
  changes = " | ".join(planShareUplink(**state)["changes"])

  assert "uci set dhcp.lan=dhcp" in changes
  assert "dhcp.lan.start='100'" in changes

def test_anUnmasqueradedUplinkIsCaught():
  zones = [
    {"section": "@zone[0]", "name": "lan", "networks": ["lan"], "masquerade": False},
    {"section": "@zone[1]", "name": "wwan", "networks": ["wwan"], "masquerade": False},
  ]
  changes = " | ".join(planShareUplink(**dict(FIXED, zones = zones))["changes"])

  assert "firewall.@zone[1].masq='1'" in changes

def test_aMissingForwardingIsAdded():
  changes = " | ".join(planShareUplink(**dict(FIXED, forwardings = []))["changes"])

  assert "uci add firewall forwarding" in changes
  assert "@forwarding[-1].src='lan'" in changes
  assert "@forwarding[-1].dest='wwan'" in changes

def test_planningWithoutAnUplinkRefuses():
  state = dict(FIXED, routeOutput = "192.168.10.0/24 dev br-lan scope link")

  with pytest.raises(BridgeError) as err:
    planShareUplink(**state)

  assert "join one first" in str(err.value)

def test_planningWithoutALanRefuses():
  state = dict(FIXED, interfaces = [entry for entry in FIXED["interfaces"] if entry["name"] != "lan"])

  with pytest.raises(BridgeError):
    planShareUplink(**state)

def test_runningTheSamePlanTwiceIsANoOp():
  # the plan reads current state, so a repaired router produces nothing the second time
  assert planShareUplink(**FIXED)["changes"] == []

# ---------------------------------------------------------------------------- #
# joining an upstream network
# ---------------------------------------------------------------------------- #

def joinPlan(**overrides):
  arguments = dict(
    radios = FIXED["radios"], interfaces = FIXED["interfaces"], zones = FIXED["zones"],
    ssid = "CafeWifi", password = "hunter2", radio = None, encryption = "psk2"
  )
  arguments.update(overrides)

  return planJoinUpstream(**arguments)

def test_joiningPicksARadioNotAlreadyServingClients():
  # one radio doing both halves the throughput and drops every client when it retunes
  changes = " | ".join(joinPlan()["changes"])

  assert "device='radio1'" in changes

def test_anExplicitRadioIsHonoured():
  assert "device='radio0'" in " | ".join(joinPlan(radio = "radio0")["changes"])

def test_anUnknownRadioIsRefused():
  with pytest.raises(BridgeError):
    joinPlan(radio = "radio9")

def test_joiningSetsClientMode():
  changes = " | ".join(joinPlan()["changes"])

  assert "mode='sta'" in changes
  assert "ssid='CafeWifi'" in changes

def test_anOpenNetworkNeedsNoPassword():
  changes = " | ".join(joinPlan(encryption = "none", password = "")["changes"])

  assert ".key=" not in changes

def test_anEncryptedNetworkWithoutAPasswordIsRefused():
  with pytest.raises(BridgeError) as err:
    joinPlan(password = "")

  assert "needs a password" in str(err.value)

def test_anUnknownEncryptionIsRefusedWithTheChoices():
  with pytest.raises(BridgeError) as err:
    joinPlan(encryption = "wep")

  assert "psk2" in str(err.value)

def test_thePasswordNeverAppearsInTheReasons():
  # the reasons are written to be read back to a human, and often logged with the rest
  plan = joinPlan()

  assert "hunter2" not in " ".join(plan["reasons"])
  assert "hunter2" not in " ".join(plan["warnings"])

def test_joiningWarnsThatTheRadioDrops():
  assert any("drops briefly" in warning for warning in joinPlan()["warnings"])

def test_joiningWarnsAboutASubnetCollisionAhead():
  # the upstream may hand out the same subnet this router serves
  assert any("collision" in warning for warning in joinPlan()["warnings"])

def test_theUplinkInterfaceIsCreatedWhenMissing():
  interfaces = [entry for entry in FIXED["interfaces"] if entry["name"] != "wwan"]
  changes = " | ".join(joinPlan(interfaces = interfaces)["changes"])

  assert "uci set network.wwan=interface" in changes
  assert "network.wwan.proto='dhcp'" in changes

def test_anExistingUplinkInterfaceIsNotRecreated():
  assert "uci set network.wwan=interface" not in " | ".join(joinPlan()["changes"])
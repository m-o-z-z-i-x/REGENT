import pytest

from regent.firewall import asList, collectZones, collectForwardings, getFirewallZones, getFirewallRuleset
from regent.ssh import CommandResult
from regent.config import Settings
from regent.uci import buildShow, parseShow
from tests.fakes import FakeSession

# captured verbatim from the live Archer C59 after the subnet fix
REAL_FIREWALL = """firewall.@defaults[0]=defaults
firewall.@defaults[0].syn_flood='1'
firewall.@zone[0]=zone
firewall.@zone[0].name='lan'
firewall.@zone[0].input='ACCEPT'
firewall.@zone[0].output='ACCEPT'
firewall.@zone[0].forward='ACCEPT'
firewall.@zone[0].network='lan'
firewall.@zone[1]=zone
firewall.@zone[1].name='wwan'
firewall.@zone[1].input='REJECT'
firewall.@zone[1].output='ACCEPT'
firewall.@zone[1].forward='REJECT'
firewall.@zone[1].masq='1'
firewall.@zone[1].network='wwan'
firewall.@forwarding[0]=forwarding
firewall.@forwarding[0].src='lan'
firewall.@forwarding[0].dest='wwan'"""

def makeSettings():
  return Settings(
    host = "192.168.10.1", user = "root", port = 22, keyPath = "./keys/key",
    timeout = 30, rollbackDelay = 90, writeEnabled = False
  )

def test_asListWrapsASingleValue():
  assert asList("lan") == ["lan"]

def test_asListKeepsARealList():
  assert asList(["lan", "wwan"]) == ["lan", "wwan"]

def test_asListTurnsMissingIntoEmpty():
  assert asList(None) == []

def test_collectZonesReadsTheRealConfig():
  zones = collectZones(parseShow(REAL_FIREWALL))

  assert len(zones) == 2
  lan = next(zone for zone in zones if zone["name"] == "lan")
  assert lan["networks"] == ["lan"]
  assert lan["input"] == "ACCEPT"
  assert lan["masquerade"] is False

def test_collectZonesFlagsTheMasqueradedZone():
  zones = collectZones(parseShow(REAL_FIREWALL))
  wwan = next(zone for zone in zones if zone["name"] == "wwan")

  assert wwan["masquerade"] is True
  assert wwan["input"] == "REJECT"

def test_collectZonesIgnoresNonZoneSections():
  zones = collectZones(parseShow(REAL_FIREWALL))

  assert all(zone["name"] is not None for zone in zones)

def test_collectForwardingsReadsThePairs():
  forwardings = collectForwardings(parseShow(REAL_FIREWALL))

  assert forwardings == [{"section": "@forwarding[0]", "src": "lan", "dest": "wwan"}]

async def test_getFirewallZonesReturnsBothHalves():
  session = FakeSession(responses = {
    buildShow("firewall"): CommandResult(exitCode = 0, stdout = REAL_FIREWALL, stderr = "")
  })

  result = await getFirewallZones(session, makeSettings())

  assert len(result["zones"]) == 2
  assert len(result["forwardings"]) == 1

async def test_getFirewallRulesetPrefersNftables():
  session = FakeSession(responses = {
    "nft list ruleset 2>/dev/null": CommandResult(exitCode = 0, stdout = "table inet fw4 {}", stderr = "")
  })

  result = await getFirewallRuleset(session, makeSettings())

  assert result["backend"] == "nftables"

async def test_getFirewallRulesetFallsBackToIptables():
  session = FakeSession(responses = {
    "nft list ruleset 2>/dev/null": CommandResult(exitCode = 1, stdout = "", stderr = "not found"),
    "iptables-save 2>/dev/null": CommandResult(exitCode = 0, stdout = "*filter\n:INPUT ACCEPT", stderr = "")
  })

  result = await getFirewallRuleset(session, makeSettings())

  assert result["backend"] == "iptables"
  assert "*filter" in result["ruleset"]
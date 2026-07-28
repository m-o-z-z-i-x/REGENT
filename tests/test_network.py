import pytest

from regent.network import (
  parseLeases, parseArp, mergeClients, summariseInterface,
  getInterfaces, getDhcpLeases, getConnectedClients
)
from regent.ssh import CommandResult
from regent.config import Settings
from regent.ubus import buildCall
from tests.fakes import FakeSession

# captured verbatim from the live Archer C59 running OpenWrt 23.05.4
REAL_ARP = """IP address       HW type     Flags       HW address            Mask     Device
192.168.1.1      0x1         0x2         00:00:5e:00:53:1a     *        phy1-sta0
192.168.10.229   0x1         0x2         00:00:5e:00:53:ac     *        br-lan
192.168.10.132   0x1         0x2         00:00:5e:00:53:60     *        br-lan"""

REAL_LEASES = """1785135179 00:00:5e:00:53:60 192.168.10.132 * 01:00:00:5e:00:53:60
1785134741 00:00:5e:00:53:ac 192.168.10.229 WORKSTATION 01:00:00:5e:00:53:ac"""

def makeSettings():
  return Settings(
    host = "192.168.10.1",
    user = "root",
    port = 22,
    keyPath = "./keys/key",
    timeout = 30,
    rollbackDelay = 90,
    writeEnabled = False
  )

def test_parseLeasesReadsTheRealFormat():
  leases = parseLeases(REAL_LEASES)

  assert len(leases) == 2
  assert leases[1]["mac"] == "00:00:5e:00:53:ac"
  assert leases[1]["ip"] == "192.168.10.229"
  assert leases[1]["hostname"] == "WORKSTATION"
  assert leases[1]["expiresAt"] == 1785134741

def test_parseLeasesTreatsAsteriskAsNoHostname():
  # dnsmasq writes "*" when the client sent no hostname, and it is not a name to show
  assert parseLeases(REAL_LEASES)[0]["hostname"] is None

def test_parseLeasesIgnoresShortLines():
  assert parseLeases("garbage\n\n1785 aa:bb") == []

def test_parseArpSkipsTheHeaderRow():
  entries = parseArp(REAL_ARP)

  assert len(entries) == 3
  assert all(entry["ip"] != "IP" for entry in entries)

def test_parseArpKeepsOnlyCompleteEntries():
  output = REAL_ARP + "\n192.168.10.77    0x1         0x0         00:00:00:00:00:00     *        br-lan"
  entries = parseArp(output)

  assert len(entries) == 3
  assert all(entry["ip"] != "192.168.10.77" for entry in entries)

def test_parseArpLowercasesMacsSoTheyMatchLeases():
  output = "hdr\n192.168.10.5     0x1         0x2         AA:BB:CC:DD:EE:FF     *        br-lan"

  assert parseArp(output)[0]["mac"] == "aa:bb:cc:dd:ee:ff"

def test_mergeClientsAttachesHostnamesFromLeases():
  clients = mergeClients(parseArp(REAL_ARP), parseLeases(REAL_LEASES))
  desktop = next(c for c in clients if c["mac"] == "00:00:5e:00:53:ac")

  assert desktop["hostname"] == "WORKSTATION"
  assert desktop["device"] == "br-lan"
  assert desktop["active"] is True

def test_mergeClientsKeepsTheUpstreamRouterSeenOnlyInArp():
  clients = mergeClients(parseArp(REAL_ARP), parseLeases(REAL_LEASES))
  upstream = next(c for c in clients if c["ip"] == "192.168.1.1")

  assert upstream["hostname"] is None
  assert upstream["device"] == "phy1-sta0"

def test_mergeClientsIncludesLeaseOnlyDevicesAsInactive():
  leases = parseLeases("1785 de:ad:be:ef:00:01 192.168.10.50 SLEEPY 01:de")
  clients = mergeClients([], leases)

  assert clients[0]["active"] is False
  assert clients[0]["hostname"] == "SLEEPY"

def test_summariseInterfaceDropsTheEmptyIpv6Noise():
  entry = {
    "interface": "lan", "up": True, "proto": "static", "l3_device": "br-lan",
    "uptime": 363,
    "ipv4-address": [{"address": "192.168.10.1", "mask": 24}],
    "ipv6-address": [], "ipv6-prefix": [], "route": [], "dns-server": []
  }

  summary = summariseInterface(entry)

  assert summary == {
    "name": "lan", "up": True, "proto": "static", "device": "br-lan",
    "addresses": ["192.168.10.1/24"], "uptime": 363
  }

async def test_getInterfacesSummarisesTheDump():
  session = FakeSession(responses = {
    buildCall("network.interface", "dump"): CommandResult(
      exitCode = 0,
      stdout = '{"interface":[{"interface":"lan","up":true,"proto":"static","l3_device":"br-lan","uptime":363,"ipv4-address":[{"address":"192.168.10.1","mask":24}]}]}',
      stderr = ""
    )
  })

  result = await getInterfaces(session, makeSettings())

  assert result["interfaces"][0]["name"] == "lan"
  assert result["interfaces"][0]["addresses"] == ["192.168.10.1/24"]

async def test_getDhcpLeasesReadsTheLeaseFile():
  session = FakeSession(responses = {
    "cat /tmp/dhcp.leases 2>/dev/null": CommandResult(exitCode = 0, stdout = REAL_LEASES, stderr = "")
  })

  result = await getDhcpLeases(session, makeSettings())

  assert len(result["leases"]) == 2

async def test_getConnectedClientsMergesBothSources():
  session = FakeSession(responses = {
    "cat /proc/net/arp": CommandResult(exitCode = 0, stdout = REAL_ARP, stderr = ""),
    "cat /tmp/dhcp.leases 2>/dev/null": CommandResult(exitCode = 0, stdout = REAL_LEASES, stderr = "")
  })

  result = await getConnectedClients(session, makeSettings())

  assert result["count"] == 3
  assert any(client["hostname"] == "WORKSTATION" for client in result["clients"])
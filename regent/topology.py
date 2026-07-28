# ---------------------------------------------------------------------------- #
# DESCRIPTION: one call that explains what this router is actually doing
# ---------------------------------------------------------------------------- #

# DEPENDENCIES --------------------------------------------------------------- #
from ipaddress import ip_interface, ip_network
from typing_extensions import TypedDict

from regent.uci import buildShow, parseShow
from regent.guard import READ, checkTier, annotationsFor
from regent.errors import toolSafe
from regent.network import getInterfaces, getConnectedClients
from regent.wireless import getWirelessStatus, getWirelessClients
from regent.firewall import getFirewallZones
# ---------------------------------------------------------------------------- #

# TYPES ---------------------------------------------------------------------- #
class Uplink(TypedDict):
  interface: str | None
  device: str | None
  proto: str | None
  addresses: list[str]
  gateway: str | None
  ssid: str | None
  up: bool

class LanSummary(TypedDict):
  interface: str | None
  addresses: list[str]
  dhcpEnabled: bool

class AccessPoint(TypedDict):
  ifname: str | None
  ssid: str | None
  band: str | None
  channel: str | None
  networks: list[str]
  stations: int

class TopologyReply(TypedDict):
  uplink: Uplink | None
  lans: list[LanSummary]
  accessPoints: list[AccessPoint]
  clients: int
  masqueradedZones: list[str]
  forwardings: list[str]
  warnings: list[str]
# ---------------------------------------------------------------------------- #

# LOGIC ---------------------------------------------------------------------- #
# an interface carrying the default route is the way out; everything else is local
def findUplinkName(routeOutput):
  for line in routeOutput.splitlines():
    if line.startswith("default via"):
      parts = line.split()

      return parts[4] if "dev" in parts else None

  return None

def findGateway(routeOutput):
  for line in routeOutput.splitlines():
    if line.startswith("default via"):
      return line.split()[2]

  return None

def networksOf(addresses):
  # "192.168.10.1/24" -> the /24 it sits in, so two interfaces can be compared
  networks = []

  for address in addresses:
    try:
      networks.append(ip_interface(address).network)
    except ValueError:
      continue

  return networks

def dhcpEnabledFor(dhcpSections, interfaceName):
  # dnsmasq serves an interface unless its section says ignore
  for section in dhcpSections.values():
    if section.get(".type") == "dhcp" and section.get("interface") == interfaceName:
      return section.get("ignore") != "1"

  return False

def collectAccessPoints(radios, stationCounts):
  accessPoints = []

  for radio in radios:
    for entry in radio.get("interfaces", []):
      if entry.get("mode") != "ap":
        continue

      accessPoints.append({
        "ifname": entry.get("ifname"),
        "ssid": entry.get("ssid"),
        "band": radio.get("band"),
        "channel": radio.get("channel"),
        "networks": entry.get("networks", []),
        "stations": stationCounts.get(entry.get("ifname"), 0)
      })

  return accessPoints

def findUplinkSsid(radios, device):
  for radio in radios:
    for entry in radio.get("interfaces", []):
      if entry.get("mode") == "sta" and entry.get("ifname") == device:
        return entry.get("ssid")

  return None

def analyse(interfaces, radios, stationCounts, zones, forwardings, dhcpSections, routeOutput, clientCount):
  # name the specific misconfigurations that leave a router looking healthy while nothing works
  warnings = []

  uplinkDevice = findUplinkName(routeOutput)
  gateway = findGateway(routeOutput)

  byDevice = {entry.get("device"): entry for entry in interfaces}
  uplinkEntry = byDevice.get(uplinkDevice)

  uplink = None

  if uplinkEntry:
    uplink = {
      "interface": uplinkEntry.get("name"),
      "device": uplinkDevice,
      "proto": uplinkEntry.get("proto"),
      "addresses": uplinkEntry.get("addresses", []),
      "gateway": gateway,
      "ssid": findUplinkSsid(radios, uplinkDevice),
      "up": uplinkEntry.get("up", False)
    }
  elif not uplinkDevice:
    warnings.append("no default route - the router itself has no way out")

  lans = []

  for entry in interfaces:
    name = entry.get("name")

    if name in (None, "loopback") or (uplink and name == uplink["interface"]):
      continue

    lans.append({
      "interface": name,
      "addresses": entry.get("addresses", []),
      "dhcpEnabled": dhcpEnabledFor(dhcpSections, name)
    })

  accessPoints = collectAccessPoints(radios, stationCounts)

  # lan and uplink on one subnet: the gateway points at the router itself and nothing routes
  if uplink:
    uplinkNetworks = networksOf(uplink["addresses"])

    for lan in lans:
      for lanNetwork in networksOf(lan["addresses"]):
        if lanNetwork in uplinkNetworks:
          warnings.append(
            f"{lan['interface']} ({lanNetwork}) is on the same subnet as the uplink - "
            "the default gateway resolves to this router itself and nothing routes out"
          )

    if not uplink["up"]:
      warnings.append(f"the uplink {uplink['interface']} is down")

  upNetworks = {entry.get("name") for entry in interfaces if entry.get("up")}

  for accessPoint in accessPoints:
    for network in accessPoint["networks"]:
      if network not in upNetworks:
        warnings.append(
          f"access point {accessPoint['ssid']} is attached to '{network}', which is not up - "
          "clients will associate but get no address"
        )

  for lan in lans:
    servesAp = any(lan["interface"] in ap["networks"] for ap in accessPoints)

    if not lan["dhcpEnabled"] and (servesAp or lan["interface"] == "lan"):
      warnings.append(f"dhcp is not serving {lan['interface']} - clients there must be configured by hand")

  masqueraded = [zone["name"] for zone in zones if zone.get("masquerade")]

  if uplink:
    uplinkZones = [zone["name"] for zone in zones if uplink["interface"] in zone.get("networks", [])]

    if uplinkZones and not any(name in masqueraded for name in uplinkZones):
      warnings.append(
        f"the uplink sits in zone {uplinkZones[0]}, which does not masquerade - "
        "clients behind this router cannot reach the internet"
      )

  return {
    "uplink": uplink,
    "lans": lans,
    "accessPoints": accessPoints,
    "clients": clientCount,
    "masqueradedZones": masqueraded,
    "forwardings": [f"{entry['src']} -> {entry['dest']}" for entry in forwardings],
    "warnings": warnings
  }

async def describeTopology(session, settings):
  checkTier(READ, settings)

  interfaces = (await getInterfaces(session, settings))["interfaces"]
  radios = (await getWirelessStatus(session, settings))["radios"]
  wirelessClients = (await getWirelessClients(session, settings))["interfaces"]
  firewall = await getFirewallZones(session, settings)
  clients = (await getConnectedClients(session, settings))["count"]

  dhcpSections = parseShow((await session.run(buildShow("dhcp"))).stdout)
  routeOutput = (await session.run("ip route")).stdout

  stationCounts = {entry["ifname"]: entry["count"] for entry in wirelessClients}

  return analyse(
    interfaces, radios, stationCounts,
    firewall["zones"], firewall["forwardings"],
    dhcpSections, routeOutput, clients
  )

def registerTools(mcp, session, settings):
  @mcp.tool(annotations = annotationsFor(READ, "Explain the router's topology"))
  @toolSafe
  async def routerTopology() -> TopologyReply:
    """What this router is doing: its uplink, the networks it serves, its access points,
    and any misconfiguration that would stop traffic flowing. Call this before changing
    anything, so the change is based on the router's actual state"""
    return await describeTopology(session, settings)
# ---------------------------------------------------------------------------- #
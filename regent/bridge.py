# ---------------------------------------------------------------------------- #
# DESCRIPTION: composite intents for routed client mode - join an uplink, serve clients
# ---------------------------------------------------------------------------- #

# DEPENDENCIES --------------------------------------------------------------- #
from ipaddress import ip_interface, ip_network
from typing_extensions import TypedDict

# custom
from regent.uci import buildShow, parseShow, quoteValue
from regent.guard import READ, WRITE, checkTier, annotationsFor
from regent.errors import toolSafe
from regent.apply import applyUci
from regent.topology import findUplinkName, networksOf
from regent.shell import requireSafeName
from regent.secrets import redactCommand
from regent.metadata import SLUG
# ---------------------------------------------------------------------------- #

# TYPES ---------------------------------------------------------------------- #
class IntentPlan(TypedDict):
  changes: list[str]
  reasons: list[str]
  warnings: list[str]
  alreadyCorrect: bool

class IntentResult(TypedDict):
  plan: IntentPlan
  applied: bool
  confirmed: bool
  detail: str
# ---------------------------------------------------------------------------- #

# LOGIC ---------------------------------------------------------------------- #
# candidate LAN subnets, kept away from 192.168.0/1.x where uplinks usually sit
CANDIDATE_SUBNETS = (
  "192.168.10.1", "192.168.20.1", "192.168.30.1",
  "10.10.10.1", "172.20.10.1",
)

DEFAULT_NETMASK = "255.255.255.0"

class BridgeError(Exception):
  pass

def subnetOf(address, netmask = DEFAULT_NETMASK):
  try:
    return ip_interface(f"{address}/{netmask}").network
  except ValueError:
    return None

def pickFreeSubnet(uplinkNetworks, avoid = ()):
  # lan and uplink on one subnet makes the gateway point at the router itself, so nothing routes out
  taken = set(uplinkNetworks) | set(avoid)

  for candidate in CANDIDATE_SUBNETS:
    network = subnetOf(candidate)

    if network and network not in taken:
      return candidate

  raise BridgeError("every candidate LAN subnet collides with the uplink - pick one by hand")

def findZoneFor(zones, networkName):
  for zone in zones:
    if networkName in zone.get("networks", []):
      return zone

  return None

# zone names conventionally used for the outside, in the order worth trying
OUTSIDE_ZONE_NAMES = ("wan", "wwan", "wan6", "internet")

def chooseUplinkZone(zones, uplinkName, lanZone):
  # never the lan zone. prefer one already holding the uplink, then one named like the outside
  candidates = [zone for zone in zones if zone["section"] != lanZone["section"]]

  for zone in candidates:
    if uplinkName in zone.get("networks", []):
      return zone

  for name in OUTSIDE_ZONE_NAMES:
    for zone in candidates:
      if zone.get("name") == name:
        return zone

  return candidates[0] if candidates else None

def hasForwarding(forwardings, source, destination):
  return any(entry.get("src") == source and entry.get("dest") == destination for entry in forwardings)

def findApInterfaces(radios):
  interfaces = []

  for radio in radios:
    for entry in radio.get("interfaces", []):
      if entry.get("mode") == "ap":
        interfaces.append((radio, entry))

  return interfaces

def dhcpSectionFor(dhcpSections, interfaceName):
  for name, section in dhcpSections.items():
    if section.get(".type") == "dhcp" and section.get("interface") == interfaceName:
      return name, section

  return None, None

def planShareUplink(interfaces, radios, zones, forwardings, dhcpSections, routeOutput, lanName = "lan"):
  # emits only what is missing, so running it twice is a no-op the second time.
  #
  # uci reads this as part of a path, so it must be checked rather than quoted
  lanName = requireSafeName(lanName, "interface name")

  changes = []
  reasons = []
  warnings = []

  uplinkDevice = findUplinkName(routeOutput)
  byDevice = {entry.get("device"): entry for entry in interfaces}
  uplinkEntry = byDevice.get(uplinkDevice)

  if not uplinkEntry:
    raise BridgeError("no default route - there is no uplink to share yet, join one first")

  uplinkName = uplinkEntry.get("name")
  lanEntry = next((entry for entry in interfaces if entry.get("name") == lanName), None)

  if not lanEntry:
    raise BridgeError(f"there is no '{lanName}' interface to serve clients from")

  uplinkNetworks = networksOf(uplinkEntry.get("addresses", []))
  lanNetworks = networksOf(lanEntry.get("addresses", []))

  # 1. the collision, first because everything else is pointless while it stands
  if any(network in uplinkNetworks for network in lanNetworks):
    replacement = pickFreeSubnet(uplinkNetworks)

    changes.append(f"uci set network.{lanName}.ipaddr={quoteValue(replacement)}")
    changes.append(f"uci set network.{lanName}.netmask={quoteValue(DEFAULT_NETMASK)}")
    reasons.append(
      f"{lanName} shares a subnet with the uplink, so the default gateway resolves to this "
      f"router itself and nothing routes out - moving it to {replacement}"
    )
    warnings.append(
      f"the router's address changes to {replacement}. Clients on DHCP pick it up on renewal; "
      "anything with a static address, including the machine running this, must be changed by hand "
      "or it loses the router"
    )

  # 2. access points must land on the network that actually serves addresses
  for radio, entry in findApInterfaces(radios):
    if lanName not in entry.get("networks", []):
      section = entry.get("section")

      changes.append(f"uci set wireless.{section}.network={quoteValue(lanName)}")
      reasons.append(
        f"access point {entry.get('ssid')} is attached to {entry.get('networks') or 'nothing'}, "
        f"where clients associate and then get no address - moving it to {lanName}"
      )

  # 3. something has to hand out addresses
  sectionName, section = dhcpSectionFor(dhcpSections, lanName)

  if section is None:
    changes.append(f"uci set dhcp.{lanName}=dhcp")
    changes.append(f"uci set dhcp.{lanName}.interface={quoteValue(lanName)}")
    changes.append(f"uci set dhcp.{lanName}.start='100'")
    changes.append(f"uci set dhcp.{lanName}.limit='150'")
    changes.append(f"uci set dhcp.{lanName}.leasetime='12h'")
    reasons.append(f"no dhcp server serves {lanName}, so clients would have to be configured by hand")
  elif section.get("ignore") == "1":
    changes.append(f"uci delete dhcp.{sectionName}.ignore")
    reasons.append(f"dhcp is switched off for {lanName} - turning it back on")

  # 4. the two zones must differ, or the plan would forward lan to itself
  lanZone = findZoneFor(zones, lanName)

  if not lanZone:
    raise BridgeError(f"'{lanName}' is not in any firewall zone - cannot tell how it should be treated")

  uplinkZone = chooseUplinkZone(zones, uplinkName, lanZone)

  if not uplinkZone:
    raise BridgeError(
      f"there is no firewall zone for the uplink apart from '{lanZone['name']}' - "
      "create a wan zone before sharing"
    )

  # 5. put the uplink in that zone only, dropping members that have no device
  usable = {
    entry.get("name") for entry in interfaces
    if entry.get("up") or entry.get("device")
  }

  if uplinkName not in uplinkZone.get("networks", []):
    kept = [
      name for name in uplinkZone.get("networks", [])
      if name != lanName and name in usable
    ]

    changes.append(f"uci set firewall.{uplinkZone['section']}.network={quoteValue(' '.join(kept + [uplinkName]))}")

    dropped = [name for name in uplinkZone.get("networks", []) if name not in usable and name != lanName]
    note = f", dropping {', '.join(dropped)} which no longer exist" if dropped else ""

    reasons.append(
      f"zone {uplinkZone['name']} does not contain the uplink {uplinkName}, so the rules meant "
      f"for the outside apply to nothing{note}"
    )

  if uplinkName in lanZone.get("networks", []):
    remaining = [name for name in lanZone["networks"] if name != uplinkName]

    changes.append(f"uci set firewall.{lanZone['section']}.network={quoteValue(' '.join(remaining))}")
    reasons.append(
      f"zone {lanZone['name']} contains the uplink as well as {lanName}, which treats the outside "
      "as local"
    )

  # 6. traffic leaving through the uplink has to be translated
  if not uplinkZone.get("masquerade"):
    changes.append(f"uci set firewall.{uplinkZone['section']}.masq='1'")
    reasons.append(
      f"the uplink sits in zone {uplinkZone['name']}, which does not masquerade, so replies "
      "to clients would have nowhere to return to"
    )

  # 6. and the traffic has to be allowed across
  if lanZone and not hasForwarding(forwardings, lanZone["name"], uplinkZone["name"]):
    changes.append("uci add firewall forwarding")
    changes.append(f"uci set firewall.@forwarding[-1].src={quoteValue(lanZone['name'])}")
    changes.append(f"uci set firewall.@forwarding[-1].dest={quoteValue(uplinkZone['name'])}")
    reasons.append(f"nothing forwards {lanZone['name']} to {uplinkZone['name']}, so client traffic stops at the router")

  return {
    "changes": changes,
    "reasons": reasons,
    "warnings": warnings,
    "alreadyCorrect": not changes
  }

# the interface the uplink lands on, and the section that joins it
UPLINK_NETWORK = "wwan"
UPLINK_SECTION = f"{SLUG}_uplink"

ENCRYPTIONS = ("psk2", "psk-mixed", "psk", "sae", "sae-mixed", "none")

def requireEncryption(value):
  text = str(value or "").strip().lower()

  if text not in ENCRYPTIONS:
    raise BridgeError(f"'{text[:20]}' is not a known encryption - expected one of {', '.join(ENCRYPTIONS)}")

  return text

def pickClientRadio(radios, preferred = None):
  # prefer a radio with no access point on it, since one radio doing both halves the throughput
  if preferred:
    match = next((radio for radio in radios if radio.get("radio") == preferred), None)

    if not match:
      raise BridgeError(f"there is no radio called '{preferred}' on this router")

    return match["radio"]

  free = [
    radio for radio in radios
    if not any(entry.get("mode") == "ap" for entry in radio.get("interfaces", []))
  ]

  candidates = free or radios

  if not candidates:
    raise BridgeError("this router has no wireless radios")

  return candidates[0]["radio"]

def planJoinUpstream(radios, interfaces, zones, ssid, password, radio = None, encryption = "psk2"):
  # the password is quoted as a value, and must not appear in the reasons shown to the user
  safeEncryption = requireEncryption(encryption)
  device = pickClientRadio(radios, radio)

  if safeEncryption != "none" and not password:
    raise BridgeError(f"{safeEncryption} needs a password")

  changes = [
    f"uci set wireless.{UPLINK_SECTION}=wifi-iface",
    f"uci set wireless.{UPLINK_SECTION}.device={quoteValue(device)}",
    f"uci set wireless.{UPLINK_SECTION}.mode='sta'",
    f"uci set wireless.{UPLINK_SECTION}.network={quoteValue(UPLINK_NETWORK)}",
    f"uci set wireless.{UPLINK_SECTION}.ssid={quoteValue(ssid)}",
    f"uci set wireless.{UPLINK_SECTION}.encryption={quoteValue(safeEncryption)}",
  ]

  if safeEncryption != "none":
    changes.append(f"uci set wireless.{UPLINK_SECTION}.key={quoteValue(password)}")

  reasons = [f"joining '{ssid}' as a client on {device}, landing on the {UPLINK_NETWORK} interface"]

  existing = {entry.get("name") for entry in interfaces}

  if UPLINK_NETWORK not in existing:
    changes.append(f"uci set network.{UPLINK_NETWORK}=interface")
    changes.append(f"uci set network.{UPLINK_NETWORK}.proto='dhcp'")
    reasons.append(f"creating the {UPLINK_NETWORK} interface to take an address from the upstream")

  outside = next(
    (zone for zone in zones if zone.get("name") in OUTSIDE_ZONE_NAMES),
    None
  )

  if outside and UPLINK_NETWORK not in outside.get("networks", []):
    networks = list(outside.get("networks", [])) + [UPLINK_NETWORK]

    changes.append(f"uci set firewall.{outside['section']}.network={quoteValue(' '.join(networks))}")
    changes.append(f"uci set firewall.{outside['section']}.masq='1'")
    reasons.append(f"putting {UPLINK_NETWORK} in the {outside['name']} zone so it is treated as the outside")
  elif not outside:
    reasons.append(
      "no zone named like the outside was found - run routerShareUplink afterwards to "
      "sort the firewall out"
    )

  return {
    "changes": changes,
    "reasons": reasons,
    "warnings": [
      f"the radio {device} retunes while joining, so anything associated to it drops briefly",
      "if the upstream hands out the subnet this router already serves, run routerShareUplink "
      "afterwards - it detects that collision and moves the LAN"
    ],
    "alreadyCorrect": False
  }

async def joinUpstreamWifi(session, settings, ssid, password = "", radio = None, encryption = "psk2", dryRun = False):
  checkTier(READ if dryRun else WRITE, settings)

  from regent.wireless import getWirelessStatus
  from regent.network import getInterfaces
  from regent.firewall import getFirewallZones

  radios = (await getWirelessStatus(session, settings))["radios"]
  interfaces = (await getInterfaces(session, settings))["interfaces"]
  zones = (await getFirewallZones(session, settings))["zones"]

  plan = planJoinUpstream(radios, interfaces, zones, ssid, password, radio = radio, encryption = encryption)

  # the caller may log the plan, so the password never leaves here readable
  visible = dict(plan, changes = [redactCommand(change) for change in plan["changes"]])

  if dryRun:
    return {"plan": visible, "applied": False, "confirmed": False, "detail": "dry run - nothing was applied"}

  outcome = await applyUci(session, settings, plan["changes"])

  return {
    "plan": visible,
    "applied": outcome["applied"],
    "confirmed": outcome["confirmed"],
    "detail": outcome["detail"]
  }

async def gatherState(session, settings):
  from regent.network import getInterfaces
  from regent.wireless import getWirelessStatus
  from regent.firewall import getFirewallZones

  interfaces = (await getInterfaces(session, settings))["interfaces"]
  radios = (await getWirelessStatus(session, settings))["radios"]
  firewall = await getFirewallZones(session, settings)
  dhcpSections = parseShow((await session.run(buildShow("dhcp"))).stdout)
  routeOutput = (await session.run("ip route")).stdout

  return interfaces, radios, firewall["zones"], firewall["forwardings"], dhcpSections, routeOutput

async def shareUplinkWithClients(session, settings, lanName = "lan", dryRun = False):
  checkTier(READ if dryRun else WRITE, settings)

  state = await gatherState(session, settings)
  plan = planShareUplink(*state, lanName = lanName)

  if plan["alreadyCorrect"]:
    return {
      "plan": plan, "applied": False, "confirmed": False,
      "detail": "nothing to do - the uplink is already shared with clients"
    }

  if dryRun:
    return {"plan": plan, "applied": False, "confirmed": False, "detail": "dry run - nothing was applied"}

  outcome = await applyUci(session, settings, plan["changes"])

  return {
    "plan": plan,
    "applied": outcome["applied"],
    "confirmed": outcome["confirmed"],
    "detail": outcome["detail"]
  }

def registerTools(mcp, session, settings):
  @mcp.tool(annotations = annotationsFor(READ, "Plan sharing the uplink"))
  @toolSafe
  async def routerPlanShareUplink(lanName: str = "lan") -> IntentResult:
    """Work out what would have to change for every client on LAN and Wi-Fi to reach the
    internet through this router's uplink, without changing anything"""
    return await shareUplinkWithClients(session, settings, lanName = lanName, dryRun = True)

  @mcp.tool(annotations = annotationsFor(WRITE, "Join an upstream Wi-Fi"))
  @toolSafe
  async def routerJoinUpstreamWifi(
    ssid: str,
    password: str = "",
    radio: str | None = None,
    encryption: str = "psk2",
    dryRun: bool = False
  ) -> IntentResult:
    """Join an upstream Wi-Fi network as a client, so this router can share it onwards.

    Picks a radio that is not already serving an access point, since one radio doing both
    halves the throughput. Creates the uplink interface and puts it in the outside zone.
    The password is never returned or logged. Run routerShareUplink afterwards to hand
    the connection to clients"""
    return await joinUpstreamWifi(
      session, settings, ssid, password = password,
      radio = radio, encryption = encryption, dryRun = dryRun
    )

  @mcp.tool(annotations = annotationsFor(WRITE, "Share the uplink with clients"))
  @toolSafe
  async def routerShareUplink(lanName: str = "lan") -> IntentResult:
    """Give every client on LAN and Wi-Fi internet through this router's uplink.

    Moves the LAN off a subnet that collides with the uplink, attaches access points to
    the network that serves addresses, turns DHCP on, masquerades the uplink and allows
    forwarding. Runs under the rollback watchdog and does nothing if already correct"""
    return await shareUplinkWithClients(session, settings, lanName = lanName)
# ---------------------------------------------------------------------------- #
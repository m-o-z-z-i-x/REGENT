# ---------------------------------------------------------------------------- #
# DESCRIPTION: network domain - interfaces, dhcp leases and connected clients
# ---------------------------------------------------------------------------- #

# DEPENDENCIES --------------------------------------------------------------- #
from typing_extensions import TypedDict

from regent.ubus import buildCall, parseReply
from regent.guard import READ, checkTier, annotationsFor
from regent.errors import toolSafe
# ---------------------------------------------------------------------------- #

# TYPES ---------------------------------------------------------------------- #
# these become the outputSchema clients see, so changing a field changes the api
class InterfaceSummary(TypedDict):
  name: str | None
  up: bool
  proto: str | None
  device: str | None
  addresses: list[str]
  uptime: int

class Lease(TypedDict):
  mac: str
  ip: str
  hostname: str | None
  expiresAt: int | None

class Client(TypedDict):
  mac: str
  ip: str
  hostname: str | None
  device: str | None
  active: bool

class InterfacesReply(TypedDict):
  interfaces: list[InterfaceSummary]

class LeasesReply(TypedDict):
  leases: list[Lease]

class ClientsReply(TypedDict):
  clients: list[Client]
  count: int
# ---------------------------------------------------------------------------- #

# LOGIC ---------------------------------------------------------------------- #
LEASES_PATH = "/tmp/dhcp.leases"
ARP_PATH = "/proc/net/arp"

# arp flag 0x2 means the entry is complete; anything else is stale or incomplete
ARP_COMPLETE = "0x2"

def summariseInterface(entry):
  # the raw dump has a dozen empty ipv6 fields per interface, so keep only what is asked about
  addresses = [f"{a['address']}/{a['mask']}" for a in entry.get("ipv4-address", [])]

  return {
    "name": entry.get("interface"),
    "up": entry.get("up", False),
    "proto": entry.get("proto"),
    "device": entry.get("l3_device") or entry.get("device"),
    "addresses": addresses,
    "uptime": entry.get("uptime", 0)
  }

def parseLeases(output):
  # lease line: "<expiry> <mac> <ip> <hostname> <clientid>", where "*" means no hostname
  leases = []

  for line in output.splitlines():
    parts = line.split()

    if len(parts) < 4:
      continue

    expiry, mac, ip, hostname = parts[0], parts[1], parts[2], parts[3]

    leases.append({
      "mac": mac.lower(),
      "ip": ip,
      "hostname": None if hostname == "*" else hostname,
      "expiresAt": int(expiry) if expiry.isdigit() else None
    })

  return leases

def parseArp(output):
  # skip the header and incomplete entries, which are unanswered probes rather than devices
  entries = []

  for line in output.splitlines()[1:]:
    parts = line.split()

    if len(parts) < 6 or parts[2] != ARP_COMPLETE:
      continue

    entries.append({"ip": parts[0], "mac": parts[3].lower(), "device": parts[5]})

  return entries

def mergeClients(arpEntries, leases):
  # arp shows who is on the wire now, leases add hostnames and quiet devices. matched by mac
  byMac = {}

  for entry in arpEntries:
    byMac[entry["mac"]] = {
      "mac": entry["mac"],
      "ip": entry["ip"],
      "hostname": None,
      "device": entry["device"],
      "active": True
    }

  for lease in leases:
    existing = byMac.get(lease["mac"])

    if existing:
      existing["hostname"] = lease["hostname"]
    else:
      byMac[lease["mac"]] = {
        "mac": lease["mac"],
        "ip": lease["ip"],
        "hostname": lease["hostname"],
        "device": None,
        "active": False
      }

  return sorted(byMac.values(), key = lambda client: client["ip"])

async def getInterfaces(session, settings):
  checkTier(READ, settings)

  reply = parseReply((await session.run(buildCall("network.interface", "dump"))).stdout)

  return {"interfaces": [summariseInterface(entry) for entry in reply.get("interface", [])]}

async def getDhcpLeases(session, settings):
  checkTier(READ, settings)

  result = await session.run(f"cat {LEASES_PATH} 2>/dev/null")

  return {"leases": parseLeases(result.stdout)}

async def getConnectedClients(session, settings):
  checkTier(READ, settings)

  arpResult = await session.run(f"cat {ARP_PATH}")
  leaseResult = await session.run(f"cat {LEASES_PATH} 2>/dev/null")

  clients = mergeClients(parseArp(arpResult.stdout), parseLeases(leaseResult.stdout))

  return {"clients": clients, "count": len(clients)}

def registerTools(mcp, session, settings):
  @mcp.tool(annotations = annotationsFor(READ, "Network interfaces"))
  @toolSafe
  async def routerInterfaces() -> InterfacesReply:
    """Network interfaces on the router: state, protocol, device and addresses"""
    return await getInterfaces(session, settings)

  @mcp.tool(annotations = annotationsFor(READ, "DHCP leases"))
  @toolSafe
  async def routerDhcpLeases() -> LeasesReply:
    """Current DHCP leases the router has handed out"""
    return await getDhcpLeases(session, settings)

  @mcp.tool(annotations = annotationsFor(READ, "Connected clients"))
  @toolSafe
  async def routerClients() -> ClientsReply:
    """Devices on the router's networks, merging the ARP table with DHCP leases"""
    return await getConnectedClients(session, settings)
# ---------------------------------------------------------------------------- #
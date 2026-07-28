# ---------------------------------------------------------------------------- #
# DESCRIPTION: wireless domain - radio status and associated stations
# ---------------------------------------------------------------------------- #

# DEPENDENCIES --------------------------------------------------------------- #
import re
from typing_extensions import TypedDict

from regent.ubus import buildCall, parseReply
from regent.guard import READ, checkTier, annotationsFor
from regent.errors import toolSafe
# ---------------------------------------------------------------------------- #

# TYPES ---------------------------------------------------------------------- #
# there is no key field here: the psk is dropped when the reply is parsed
class WirelessInterface(TypedDict):
  section: str | None
  ifname: str | None
  mode: str | None
  ssid: str | None
  encryption: str | None
  networks: list[str]

class Radio(TypedDict):
  radio: str
  up: bool
  disabled: bool
  band: str | None
  channel: str | None
  htmode: str | None
  country: str | None
  interfaces: list[WirelessInterface]

class Station(TypedDict):
  mac: str
  signalDbm: int
  noiseDbm: int
  snr: int
  inactiveMs: int
  rxMbits: float | None
  txMbits: float | None

class InterfaceStations(TypedDict):
  ifname: str | None
  ssid: str | None
  mode: str | None
  stations: list[Station]
  count: int

class WirelessReply(TypedDict):
  radios: list[Radio]

class WirelessClientsReply(TypedDict):
  interfaces: list[InterfaceStations]
# ---------------------------------------------------------------------------- #

# LOGIC ---------------------------------------------------------------------- #
# ubus returns the psk in clear text, so it is removed before anything else sees it
SECRET_KEYS = ("key", "wpa_psk", "password", "auth_secret")

STATION_HEAD = re.compile(
  r"^([0-9A-Fa-f:]{17})\s+(-?\d+) dBm\s*/\s*(-?\d+) dBm\s*\(SNR (\d+)\)\s+(\d+) ms ago"
)
STATION_RATE = re.compile(r"^\s+(RX|TX):\s+([\d.]+) MBit/s(.*?)\s+(\d+) Pkts\.")

def redactSecrets(config):
  return {key: value for key, value in config.items() if key not in SECRET_KEYS}

def summariseRadio(name, radio):
  config = radio.get("config", {})
  interfaces = []

  for entry in radio.get("interfaces", []):
    entryConfig = entry.get("config", {})

    interfaces.append({
      "section": entry.get("section"),
      "ifname": entry.get("ifname"),
      "mode": entryConfig.get("mode"),
      "ssid": entryConfig.get("ssid"),
      "encryption": entryConfig.get("encryption"),
      "networks": entryConfig.get("network", [])
    })

  return {
    "radio": name,
    "up": radio.get("up", False),
    "disabled": radio.get("disabled", False),
    "band": config.get("band"),
    "channel": config.get("channel"),
    "htmode": config.get("htmode"),
    "country": config.get("country"),
    "interfaces": interfaces
  }

def parseAssoclist(output):
  # iwinfo prints a header line per station followed by indented RX/TX lines
  stations = []

  for line in output.splitlines():
    head = STATION_HEAD.match(line)

    if head:
      mac, signal, noise, snr, ago = head.groups()

      stations.append({
        "mac": mac.lower(),
        "signalDbm": int(signal),
        "noiseDbm": int(noise),
        "snr": int(snr),
        "inactiveMs": int(ago),
        "rxMbits": None,
        "txMbits": None
      })

      continue

    rate = STATION_RATE.match(line)

    if rate and stations:
      direction, mbits, _, _ = rate.groups()
      stations[-1]["rxMbits" if direction == "RX" else "txMbits"] = float(mbits)

  return stations

async def getWirelessStatus(session, settings):
  checkTier(READ, settings)

  reply = parseReply((await session.run(buildCall("network.wireless", "status"))).stdout)

  return {"radios": [summariseRadio(name, radio) for name, radio in sorted(reply.items())]}

async def getWirelessClients(session, settings):
  checkTier(READ, settings)

  reply = parseReply((await session.run(buildCall("network.wireless", "status"))).stdout)
  perInterface = []

  for name, radio in sorted(reply.items()):
    for entry in radio.get("interfaces", []):
      ifname = entry.get("ifname")

      if not ifname:
        continue

      result = await session.run(f"iwinfo {ifname} assoclist 2>/dev/null")
      stations = parseAssoclist(result.stdout)

      perInterface.append({
        "ifname": ifname,
        "ssid": entry.get("config", {}).get("ssid"),
        "mode": entry.get("config", {}).get("mode"),
        "stations": stations,
        "count": len(stations)
      })

  return {"interfaces": perInterface}

def registerTools(mcp, session, settings):
  @mcp.tool(annotations = annotationsFor(READ, "Wireless radios"))
  @toolSafe
  async def routerWireless() -> WirelessReply:
    """Radios and wireless interfaces: band, channel, mode and SSID. Passwords are never returned"""
    return await getWirelessStatus(session, settings)

  @mcp.tool(annotations = annotationsFor(READ, "Wireless clients"))
  @toolSafe
  async def routerWirelessClients() -> WirelessClientsReply:
    """Stations associated to each wireless interface, with signal strength and rates"""
    return await getWirelessClients(session, settings)
# ---------------------------------------------------------------------------- #
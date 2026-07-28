# ---------------------------------------------------------------------------- #
# DESCRIPTION: firewall domain - zones, forwardings and the live nftables ruleset
# ---------------------------------------------------------------------------- #

# DEPENDENCIES --------------------------------------------------------------- #
from typing_extensions import TypedDict

from regent.uci import buildShow, parseShow
from regent.guard import READ, checkTier, annotationsFor
from regent.errors import toolSafe
# ---------------------------------------------------------------------------- #

# TYPES ---------------------------------------------------------------------- #
class Zone(TypedDict):
  section: str
  name: str | None
  networks: list[str]
  input: str | None
  output: str | None
  forward: str | None
  masquerade: bool

class Forwarding(TypedDict):
  section: str
  src: str | None
  dest: str | None

class ZonesReply(TypedDict):
  zones: list[Zone]
  forwardings: list[Forwarding]

class RulesetReply(TypedDict):
  backend: str
  ruleset: str
# ---------------------------------------------------------------------------- #

# LOGIC ---------------------------------------------------------------------- #
def asList(value):
  # uci gives a bare string for a single-item list and a real list for several
  if value is None:
    return []

  return value if isinstance(value, list) else [value]

def collectZones(sections):
  zones = []

  for name, section in sections.items():
    if section.get(".type") != "zone":
      continue

    zones.append({
      "section": name,
      "name": section.get("name"),
      "networks": asList(section.get("network")),
      "input": section.get("input"),
      "output": section.get("output"),
      "forward": section.get("forward"),
      "masquerade": section.get("masq") == "1"
    })

  return sorted(zones, key = lambda zone: zone["name"] or "")

def collectForwardings(sections):
  forwardings = [
    {"section": name, "src": section.get("src"), "dest": section.get("dest")}
    for name, section in sections.items()
    if section.get(".type") == "forwarding"
  ]

  return sorted(forwardings, key = lambda entry: (entry["src"] or "", entry["dest"] or ""))

async def getFirewallZones(session, settings):
  checkTier(READ, settings)

  sections = parseShow((await session.run(buildShow("firewall"))).stdout)

  return {"zones": collectZones(sections), "forwardings": collectForwardings(sections)}

async def getFirewallRuleset(session, settings):
  checkTier(READ, settings)

  result = await session.run("nft list ruleset 2>/dev/null")

  if not result.stdout:
    # pre-fw4 images still use iptables; fall back rather than returning nothing
    result = await session.run("iptables-save 2>/dev/null")

    return {"backend": "iptables", "ruleset": result.stdout}

  return {"backend": "nftables", "ruleset": result.stdout}

def registerTools(mcp, session, settings):
  @mcp.tool(annotations = annotationsFor(READ, "Firewall zones"))
  @toolSafe
  async def routerFirewallZones() -> ZonesReply:
    """Firewall zones and the forwardings between them, including which are masqueraded"""
    return await getFirewallZones(session, settings)

  @mcp.tool(annotations = annotationsFor(READ, "Firewall ruleset"))
  @toolSafe
  async def routerFirewallRuleset() -> RulesetReply:
    """The live packet filter ruleset, as nftables or iptables depending on the image"""
    return await getFirewallRuleset(session, settings)
# ---------------------------------------------------------------------------- #
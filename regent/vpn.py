# ---------------------------------------------------------------------------- #
# DESCRIPTION: vpn domain - PassWall, and the traps that make it look like it works
# ---------------------------------------------------------------------------- #

# DEPENDENCIES --------------------------------------------------------------- #
import re
from typing_extensions import TypedDict

from regent.guard import READ, WRITE, checkTier, annotationsFor
from regent.errors import toolSafe
# ---------------------------------------------------------------------------- #

# TYPES ---------------------------------------------------------------------- #
class VpnNode(TypedDict):
  section: str
  remarks: str | None
  protocol: str | None
  address: str | None
  port: str | None
  selected: bool

class Problem(TypedDict):
  kind: str
  detail: str
  fix: str | None

class VpnStatus(TypedDict):
  installed: bool
  variant: str | None
  appVersion: str | None
  core: str | None
  coreVersion: str | None
  enabled: bool
  running: bool
  selectedNode: str | None
  nodes: list[VpnNode]
  problems: list[Problem]
# ---------------------------------------------------------------------------- #

# LOGIC ---------------------------------------------------------------------- #
# both generations exist in the wild and keep separate configs; detect rather than assume
VARIANTS = ("passwall2", "passwall")

# without these the service starts, logs a warning, and proxies nothing
NFT_DEPENDENCIES = ("kmod-nft-tproxy", "kmod-nft-socket", "kmod-nft-nat")
IPT_DEPENDENCIES = ("iptables-mod-tproxy", "iptables-mod-socket")

# these send DNS past dnsmasq, which disables any blocklist it holds
DNS_CONFLICT_OPTIONS = ("dns_redirect", "remote_fakedns")

VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)")

class VpnError(Exception):
  pass

def parseVersion(text):
  match = VERSION.search(text or "")

  return tuple(int(part) for part in match.groups()) if match else None

def sameGeneration(appVersion, coreVersion):
  # the app and the core must be the same generation, or one writes a shape the other cannot read
  if not appVersion or not coreVersion:
    return True

  return appVersion[0] == coreVersion[0]

def probeCommand(address, port):
  # busybox nc reports every port closed, so measure the tcp handshake instead
  return (f"curl -s -o /dev/null --connect-timeout 8 "
          f"-w '%{{time_connect}}' http://{address}:{port}/ 2>/dev/null; echo")

def readHandshake(output):
  try:
    return float(str(output).strip())
  except (TypeError, ValueError):
    return 0.0

def collectNodes(sections, selected):
  nodes = []

  for name, section in sections.items():
    if section.get(".type") != "nodes":
      continue

    nodes.append({
      "section": name,
      "remarks": section.get("remarks"),
      "protocol": section.get("protocol"),
      "address": section.get("address"),
      "port": section.get("port"),
      "selected": name == selected
    })

  return sorted(nodes, key = lambda node: node["remarks"] or node["section"])

def findGlobalSection(sections):
  for name, section in sections.items():
    if section.get(".type") == "global":
      return name

  return None

async def detectPackageManager(session):
  # openwrt 24.10 replaced opkg with apk. asking the router beats assuming
  probe = await session.run("command -v apk >/dev/null && echo apk || echo opkg")

  return probe.stdout.strip() or "opkg"

async def isPackageInstalled(session, manager, name):
  if manager == "apk":
    command = f"apk info -e {name} 2>/dev/null | grep -q . && echo yes || echo no"
  else:
    command = f"opkg list-installed 2>/dev/null | grep -q '^{name} ' && echo yes || echo no"

  return (await session.run(command)).stdout.strip() == "yes"

async def detectVariant(session):
  for variant in VARIANTS:
    probe = await session.run(f"test -x /etc/init.d/{variant} && echo yes || echo no")

    if probe.stdout.strip() == "yes":
      return variant

  return None

async def detectCore(session):
  for core, command in (("xray", "xray version"), ("sing-box", "sing-box version")):
    result = await session.run(f"{command} 2>/dev/null | head -1")

    if result.stdout.strip():
      return core, result.stdout.strip()

  return None, None

async def readAppVersion(session, manager, variant):
  if manager == "apk":
    command = f"apk info -e luci-app-{variant} 2>/dev/null | head -1"
  else:
    command = f"opkg list-installed 2>/dev/null | grep '^luci-app-{variant} '"

  return (await session.run(command)).stdout.strip()

async def findProblems(session, settings, variant, manager, appVersion, coreVersion, globalSection, selectedNode):
  problems = []

  usesNft = (await session.run("command -v nft >/dev/null && echo yes || echo no")).stdout.strip() == "yes"
  required = NFT_DEPENDENCIES if usesNft else IPT_DEPENDENCIES

  for package in required:
    if not await isPackageInstalled(session, manager, package):
      problems.append({
        "kind": "missing-dependency",
        "detail": f"{package} is not installed, so transparent proxying cannot work",
        "fix": f"install {package} - without it the service starts, logs a warning and proxies nothing"
      })

  if not sameGeneration(parseVersion(appVersion), parseVersion(coreVersion)):
    problems.append({
      "kind": "version-mismatch",
      "detail": f"the app ({appVersion.strip()}) and the proxy core ({coreVersion.strip()}) are different generations",
      "fix": "upgrade the core to match the app, or the outbound it writes will not load"
    })

  if globalSection:
    for option in DNS_CONFLICT_OPTIONS:
      value = (await session.run(f"uci get {variant}.{globalSection}.{option} 2>/dev/null")).stdout.strip()

      if value == "1":
        problems.append({
          "kind": "dns-conflict",
          "detail": f"{option} is on, so DNS bypasses dnsmasq",
          "fix": f"set {variant}.{globalSection}.{option}=0 to keep DNS with dnsmasq, or any blocklist it holds stops applying"
        })

  if not selectedNode:
    problems.append({
      "kind": "no-node",
      "detail": "no node is selected, so there is nothing to proxy through",
      "fix": "choose one of the configured nodes"
    })

  return problems

async def getVpnStatus(session, settings):
  from regent.uci import buildShow, parseShow

  checkTier(READ, settings)

  variant = await detectVariant(session)

  if not variant:
    return {
      "installed": False, "variant": None, "appVersion": None, "core": None,
      "coreVersion": None, "enabled": False, "running": False,
      "selectedNode": None, "nodes": [], "problems": []
    }

  manager = await detectPackageManager(session)
  sections = parseShow((await session.run(buildShow(variant))).stdout)
  globalSection = findGlobalSection(sections)

  enabled = False
  selectedNode = None

  if globalSection:
    enabled = sections[globalSection].get("enabled") == "1"
    selectedNode = sections[globalSection].get("node")

  core, coreVersion = await detectCore(session)
  appVersion = await readAppVersion(session, manager, variant)
  running = (await session.run("ps | grep -cE '[x]ray|[s]ing-box'")).stdout.strip() not in ("", "0")

  problems = await findProblems(
    session, settings, variant, manager, appVersion, coreVersion, globalSection, selectedNode
  )

  return {
    "installed": True,
    "variant": variant,
    "appVersion": appVersion or None,
    "core": core,
    "coreVersion": coreVersion,
    "enabled": enabled,
    "running": running,
    "selectedNode": selectedNode,
    "nodes": collectNodes(sections, selectedNode),
    "problems": problems
  }

async def probeNode(session, settings, section = None):
  from regent.uci import buildShow, parseShow

  checkTier(READ, settings)

  variant = await detectVariant(session)

  if not variant:
    raise VpnError("no passwall installation found on this router")

  sections = parseShow((await session.run(buildShow(variant))).stdout)
  globalSection = findGlobalSection(sections)
  target = section or (sections.get(globalSection, {}).get("node") if globalSection else None)

  if not target or target not in sections:
    raise VpnError("no node to probe - select one first")

  node = sections[target]
  address, port = node.get("address"), node.get("port")

  if not address or not port:
    raise VpnError(f"node {target} has no address or port")

  result = await session.run(probeCommand(address, port))
  elapsed = readHandshake(result.stdout)

  return {
    "section": target,
    "remarks": node.get("remarks"),
    "address": address,
    "port": port,
    "reachable": elapsed > 0,
    "handshakeMs": round(elapsed * 1000, 1) if elapsed > 0 else None
  }

def registerTools(mcp, session, settings):
  @mcp.tool(annotations = annotationsFor(READ, "VPN status"))
  @toolSafe
  async def routerVpnStatus() -> VpnStatus:
    """Whether a PassWall VPN is installed, configured and actually proxying.

    Reports the problems that make it look like it works while it does not: a missing
    transparent-proxy kernel module, an app and proxy core from different generations,
    DNS settings that bypass dnsmasq and any blocklist it holds, or no node selected"""
    return await getVpnStatus(session, settings)

  @mcp.tool(annotations = annotationsFor(READ, "Probe the VPN node"))
  @toolSafe
  async def routerVpnProbeNode(section: str | None = None) -> dict:
    """Check whether the VPN node accepts a TCP connection, timing the handshake.
    Defaults to the currently selected node"""
    return await probeNode(session, settings, section = section)
# ---------------------------------------------------------------------------- #
# ---------------------------------------------------------------------------- #
# DESCRIPTION: services domain - what runs on the router, and starting or stopping it
# ---------------------------------------------------------------------------- #

# DEPENDENCIES --------------------------------------------------------------- #
from typing_extensions import TypedDict

from regent.guard import READ, WRITE, checkTier, annotationsFor
from regent.errors import toolSafe
from regent.shell import requireSafeName
# ---------------------------------------------------------------------------- #

# TYPES ---------------------------------------------------------------------- #
class Service(TypedDict):
  name: str
  autostart: bool
  running: bool

class ServicesReply(TypedDict):
  services: list[Service]
  count: int

class ServiceActionReply(TypedDict):
  service: str
  action: str
  succeeded: bool
  detail: str
# ---------------------------------------------------------------------------- #

# LOGIC ---------------------------------------------------------------------- #
ACTIONS = ("start", "stop", "restart", "reload", "enable", "disable")

# stopping these takes the router off the network or closes this server's way in
SELF_HARMING = {
  "network": "stopping this drops every interface, including the one this connection uses",
  "dropbear": "this is the ssh server - stopping it ends this session and prevents the next",
  "firewall": "flushing the ruleset can cut established connections",
  "uhttpd": "this is the web interface, the fallback way in when ssh is gone",
}

# restarting these drops the connection carrying the command, so they are detached
DETACH = ("network", "firewall", "passwall", "passwall2")

class ServiceError(Exception):
  pass

def requireAction(action):
  text = str(action or "").strip().lower()

  if text not in ACTIONS:
    raise ServiceError(f"'{text[:20]}' is not a service action - expected one of {', '.join(ACTIONS)}")

  return text

def parseServices(listing, enabledOutput, runningOutput):
  # `ls /etc/init.d` gives the names; the other two are lists of names, one per line
  enabled = set(enabledOutput.split())
  running = set(runningOutput.split())

  services = []

  for name in sorted(set(listing.split())):
    if not name:
      continue

    services.append({
      "name": name,
      "autostart": name in enabled,
      "running": name in running
    })

  return services

def buildAction(name, action):
  command = f"/etc/init.d/{name} {action}"

  if name in DETACH and action in ("restart", "reload", "stop"):
    return f"nohup {command} >/dev/null 2>&1 & echo started"

  return f"{command} 2>&1"

async def listServices(session, settings):
  checkTier(READ, settings)

  listing = (await session.run("ls /etc/init.d/ 2>/dev/null")).stdout

  enabled = (await session.run(
    "for f in /etc/init.d/*; do n=$(basename $f); $f enabled 2>/dev/null && echo $n; done"
  )).stdout

  running = (await session.run(
    "ubus call service list 2>/dev/null | grep -o '\"[a-z0-9_-]*\": {' | tr -d '\": {'"
  )).stdout

  services = parseServices(listing, enabled, running)

  return {"services": services, "count": len(services)}

async def controlService(session, settings, name, action, confirm = False):
  checkTier(WRITE, settings)

  safeName = requireSafeName(name, "service name")
  safeAction = requireAction(action)

  exists = (await session.run(f"test -x /etc/init.d/{safeName} && echo yes || echo no")).stdout.strip()

  if exists != "yes":
    raise ServiceError(f"there is no service called '{safeName}' on this router")

  warning = SELF_HARMING.get(safeName)

  if warning and safeAction in ("stop", "disable") and not confirm:
    raise ServiceError(
      f"{safeAction} on '{safeName}' - {warning}. Pass confirm=True if that is what you mean"
    )

  result = await session.run(buildAction(safeName, safeAction))

  return {
    "service": safeName,
    "action": safeAction,
    "succeeded": result.ok,
    "detail": result.stdout.strip()[:300] or f"{safeAction} issued"
  }

def registerTools(mcp, session, settings):
  @mcp.tool(annotations = annotationsFor(READ, "Services"))
  @toolSafe
  async def routerServices() -> ServicesReply:
    """Every service on the router, whether it starts at boot and whether it is running"""
    return await listServices(session, settings)

  @mcp.tool(annotations = annotationsFor(WRITE, "Control a service"))
  @toolSafe
  async def routerServiceControl(name: str, action: str, confirm: bool = False) -> ServiceActionReply:
    """Start, stop, restart, reload, enable or disable a service.

    Stopping network, dropbear, firewall or uhttpd takes away the way back in, so those
    need confirm=True. Services whose restart rebuilds the firewall are detached, since
    otherwise the restart kills the connection carrying it"""
    return await controlService(session, settings, name, action, confirm = confirm)
# ---------------------------------------------------------------------------- #
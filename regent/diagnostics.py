# ---------------------------------------------------------------------------- #
# DESCRIPTION: diagnostics domain - logs and reachability probes from the router
# ---------------------------------------------------------------------------- #

# DEPENDENCIES --------------------------------------------------------------- #
import re
from typing_extensions import TypedDict

from regent.guard import READ, checkTier, annotationsFor
from regent.errors import toolSafe
from regent.secrets import redactUciOutput, redactCommand
from regent.shell import requireSafeHost, UnsafeValue
# ---------------------------------------------------------------------------- #

# TYPES ---------------------------------------------------------------------- #
class LogReply(TypedDict):
  lines: list[str]
  requested: int

class PingReply(TypedDict):
  target: str
  reachable: bool
  transmitted: int
  received: int
  lossPercent: int
  minMs: float | None
  avgMs: float | None
  maxMs: float | None

class ResolveReply(TypedDict):
  target: str
  resolved: bool
  addresses: list[str]
# ---------------------------------------------------------------------------- #

# LOGIC ---------------------------------------------------------------------- #
LOG_LIMIT = 200
PING_COUNT = 4

PING_STATS = re.compile(r"(\d+) packets transmitted, (\d+) packets received, (\d+)% packet loss")
PING_TIMES = re.compile(r"round-trip min/avg/max = ([\d.]+)/([\d.]+)/([\d.]+)")
DNS_ANSWER = re.compile(r"^Address:\s*(\S+)", re.MULTILINE)

# a hostname or address safe to pass to ping and nslookup
SAFE_TARGET = re.compile(r"^[A-Za-z0-9._:-]+$")

class TargetError(Exception):
  pass

def requireSafeTarget(target):
  # one shared checker, so every module reaching the shell follows the same rules
  try:
    return requireSafeHost(target)
  except UnsafeValue as err:
    raise TargetError(str(err)) from err

def parsePing(output):
  stats = PING_STATS.search(output)
  times = PING_TIMES.search(output)

  result = {
    "transmitted": int(stats.group(1)) if stats else 0,
    "received": int(stats.group(2)) if stats else 0,
    "lossPercent": int(stats.group(3)) if stats else 100,
    "minMs": None,
    "avgMs": None,
    "maxMs": None
  }

  if times:
    result["minMs"], result["avgMs"], result["maxMs"] = (float(value) for value in times.groups())

  return result

def parseResolve(output):
  # busybox nslookup prints its own address first, so only take what comes after it
  _, _, answers = output.partition("Non-authoritative answer:")
  body = answers or output

  return [match.group(1) for match in DNS_ANSWER.finditer(body)]

async def getSystemLog(session, settings, lines = 50):
  checkTier(READ, settings)

  count = max(1, min(int(lines), LOG_LIMIT))
  result = await session.run(f"logread -l {count}")

  # services write credentials into the log, so it is redacted like any other output
  redacted = redactUciOutput(redactCommand(result.stdout))

  return {"lines": redacted.splitlines(), "requested": count}

async def pingHost(session, settings, target, count = PING_COUNT):
  checkTier(READ, settings)

  host = requireSafeTarget(target)
  packets = max(1, min(int(count), 10))
  result = await session.run(f"ping -c {packets} -W 2 {host} 2>&1")
  parsed = parsePing(result.stdout)

  return {"target": host, "reachable": parsed["received"] > 0, **parsed}

async def resolveHostname(session, settings, target):
  checkTier(READ, settings)

  host = requireSafeTarget(target)
  result = await session.run(f"nslookup {host} 2>&1")
  addresses = parseResolve(result.stdout)

  return {"target": host, "resolved": bool(addresses), "addresses": addresses}

def registerTools(mcp, session, settings):
  @mcp.tool(annotations = annotationsFor(READ, "System log"))
  @toolSafe
  async def routerLog(lines: int = 50) -> LogReply:
    """Recent system log lines from the router"""
    return await getSystemLog(session, settings, lines = lines)

  @mcp.tool(annotations = annotationsFor(READ, "Ping from the router"))
  @toolSafe
  async def routerPing(target: str, count: int = PING_COUNT) -> PingReply:
    """Ping a host from the router, to test its connectivity rather than yours"""
    return await pingHost(session, settings, target, count = count)

  @mcp.tool(annotations = annotationsFor(READ, "Resolve a hostname"))
  @toolSafe
  async def routerResolve(target: str) -> ResolveReply:
    """Resolve a hostname using the router's own DNS"""
    return await resolveHostname(session, settings, target)
# ---------------------------------------------------------------------------- #
# ---------------------------------------------------------------------------- #
# DESCRIPTION: system level tools - board info, arbitrary shell, reboot
# ---------------------------------------------------------------------------- #

# DEPENDENCIES --------------------------------------------------------------- #
from typing import Any
from typing_extensions import TypedDict

from regent.ubus import buildCall, parseReply
from regent.guard import READ, WRITE, DESTRUCTIVE, checkTier, annotationsFor
from regent.errors import toolSafe
from regent.secrets import redactUciOutput, countRedactions
from regent.shell import requireSafePath
# ---------------------------------------------------------------------------- #

# TYPES ---------------------------------------------------------------------- #
class ExecReply(TypedDict):
  exitCode: int
  stdout: str
  stderr: str
  redacted: int

class RebootReply(TypedDict):
  rebooting: bool
  detail: str
# ---------------------------------------------------------------------------- #

# LOGIC ---------------------------------------------------------------------- #
async def getSystemInfo(session, settings):
  # board carries the identity, info the live numbers. both are wanted as one answer
  checkTier(READ, settings)

  board = parseReply((await session.run(buildCall("system", "board"))).stdout)
  info = parseReply((await session.run(buildCall("system", "info"))).stdout)

  return {**board, **info}

async def execCommand(session, settings, command):
  # the escape hatch: anything reachable over ssh is reachable here, hence the write gate
  checkTier(WRITE, settings)

  result = await session.run(command)

  # this runs arbitrary commands, so the filter sits on the output rather than a known shape
  stdout = redactUciOutput(result.stdout)
  stderr = redactUciOutput(result.stderr)

  return {
    "exitCode": result.exitCode,
    "stdout": stdout,
    "stderr": stderr,
    "redacted": countRedactions(stdout) + countRedactions(stderr)
  }

async def factoryReset(session, settings, confirm = False):
  # wipes the overlay and reboots, including the ssh key that makes this connection possible
  checkTier(DESTRUCTIVE, settings, confirmed = confirm)

  await session.run("nohup sh -c 'sleep 2; firstboot -y && reboot' >/dev/null 2>&1 &")

  return {
    "rebooting": True,
    "detail": (
      "factory reset started. Every configuration is erased, including the authorized ssh key "
      "this connection uses - expect the router at 192.168.1.1 with no key installed"
    )
  }

async def upgradeFirmware(session, settings, imagePath, keepSettings = True, confirm = False):
  # an interrupted or mismatched sysupgrade is how routers become paperweights
  checkTier(DESTRUCTIVE, settings, confirmed = confirm)

  safePath = requireSafePath(imagePath)

  exists = (await session.run(f"test -f {safePath} && echo yes || echo no")).stdout.strip()

  if exists != "yes":
    raise SystemError(f"there is no image at {safePath} - upload it to the router first")

  # sysupgrade verifies the image against the board, and that check is never skipped
  check = await session.run(f"sysupgrade -T {safePath} 2>&1")

  if check.exitCode != 0:
    raise SystemError(
      f"the image at {safePath} is not valid for this board: {check.stdout.strip()[:200]}"
    )

  size = (await session.run(f"wc -c < {safePath}")).stdout.strip()
  flag = "" if keepSettings else "-n "

  await session.run(f"nohup sh -c 'sleep 2; sysupgrade {flag}{safePath}' >/dev/null 2>&1 &")

  return {
    "rebooting": True,
    "detail": (
      f"flashing {safePath} ({int(size) // 1024 if size.isdigit() else '?'} KB), "
      f"{'keeping' if keepSettings else 'discarding'} settings. "
      "The router will be unreachable for several minutes - do not cut its power"
    )
  }

async def rebootDevice(session, settings, confirm = False):
  checkTier(DESTRUCTIVE, settings, confirmed = confirm)

  await session.run("reboot")

  return {
    "rebooting": True,
    "detail": "the router is restarting - expect the connection to drop for a minute"
  }

def registerTools(mcp, session, settings):
  # toolSafe goes under mcp.tool, so the schema is built from the real signature
  @mcp.tool(annotations = annotationsFor(READ, "Router system info"))
  @toolSafe
  async def routerSystemInfo() -> dict[str, Any]:
    """Model, firmware version, uptime, load and memory of the OpenWrt router"""
    # whatever the firmware returns. a fixed field list would drop what another build adds
    return await getSystemInfo(session, settings)

  @mcp.tool(annotations = annotationsFor(WRITE, "Run a shell command on the router"))
  @toolSafe
  async def routerExec(command: str) -> ExecReply:
    """Run an arbitrary shell command on the router. Requires the write gate"""
    return await execCommand(session, settings, command)

  @mcp.tool(annotations = annotationsFor(DESTRUCTIVE, "Reboot the router"))
  @toolSafe
  async def routerReboot(confirm: bool = False) -> RebootReply:
    """Reboot the router. Requires the write gate and confirm=True"""
    return await rebootDevice(session, settings, confirm = confirm)

  @mcp.tool(annotations = annotationsFor(DESTRUCTIVE, "Factory reset"))
  @toolSafe
  async def routerFactoryReset(confirm: bool = False) -> RebootReply:
    """Erase every setting and reboot into a fresh install.

    This removes the authorized ssh key this connection depends on. Take a backup with
    routerBackupCreate first, and note it will be erased too unless it sits on storage
    the reset does not touch. Requires the write gate and confirm=True"""
    return await factoryReset(session, settings, confirm = confirm)

  @mcp.tool(annotations = annotationsFor(DESTRUCTIVE, "Flash firmware"))
  @toolSafe
  async def routerFirmwareUpgrade(imagePath: str, keepSettings: bool = True, confirm: bool = False) -> RebootReply:
    """Flash a firmware image already uploaded to the router.

    The image is verified against this board before anything is written, because writing
    the wrong one is how a router becomes a paperweight. Requires the write gate and
    confirm=True"""
    return await upgradeFirmware(session, settings, imagePath, keepSettings = keepSettings, confirm = confirm)
# ---------------------------------------------------------------------------- #
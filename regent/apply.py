# ---------------------------------------------------------------------------- #
# DESCRIPTION: apply model-composed uci changes under the rollback watchdog
# ---------------------------------------------------------------------------- #

# DEPENDENCIES --------------------------------------------------------------- #
import re
from typing_extensions import TypedDict

# custom
from regent.uci import buildCommit, buildRevert
from regent.guard import WRITE, checkTier, annotationsFor
from regent.errors import toolSafe
from regent.secrets import redactUciOutput
from regent.rollback import withRollback
# ---------------------------------------------------------------------------- #

# TYPES ---------------------------------------------------------------------- #
class ApplyReply(TypedDict):
  applied: bool
  confirmed: bool
  dryRun: bool
  configs: list[str]
  staged: list[str]
  detail: str
# ---------------------------------------------------------------------------- #

# LOGIC ---------------------------------------------------------------------- #
class ApplyError(Exception):
  pass

# only operations that stage a config change. running programs belongs in routerExec
ALLOWED_ACTIONS = ("set", "delete", "add", "add_list", "del_list", "rename", "reorder")

# a staged change cannot smuggle a second command onto the line
FORBIDDEN = (";", "|", "&", "`", "$(", ">", "<", "\n", "\r")

COMMAND = re.compile(r"^uci\s+(\w+)\s+(\S+)")

# a committed change does nothing until its service reloads, so every config maps to one
RELOAD_COMMANDS = {
  "network": "/etc/init.d/network reload",
  "firewall": "/etc/init.d/firewall reload",
  "dhcp": "/etc/init.d/dnsmasq restart",
  "wireless": "wifi reload",
  "system": "/etc/init.d/system reload",
  "dropbear": "/etc/init.d/dropbear restart",
  "adblock": "/etc/init.d/adblock reload",
  # restarting passwall drops connections, so it is detached to outlive the ssh session
  "passwall2": "nohup /etc/init.d/passwall2 restart >/dev/null 2>&1 &",
  "passwall": "nohup /etc/init.d/passwall restart >/dev/null 2>&1 &"
}

# changing any of these can cut the link this server is using
RISKY_CONFIGS = ("network", "firewall", "wireless")

def validateChange(change):
  text = str(change).strip()

  if not text.startswith("uci "):
    raise ApplyError(f"only uci commands belong here, got: {text[:60]} - use routerExec for anything else")

  for token in FORBIDDEN:
    if token in text:
      raise ApplyError(f"'{token}' is not allowed in a staged change - one command per entry, use routerExec if you need a shell")

  match = COMMAND.match(text)

  if not match:
    raise ApplyError(f"cannot read a uci action out of: {text[:60]}")

  action, target = match.groups()

  if action not in ALLOWED_ACTIONS:
    raise ApplyError(f"'uci {action}' does not stage a change - allowed: {', '.join(ALLOWED_ACTIONS)}")

  config = target.split(".")[0].split("=")[0]

  if not config:
    raise ApplyError(f"cannot tell which config '{text[:60]}' touches")

  return config

def planConfigs(changes):
  # work out the affected configs from the changes, so the snapshot always matches them
  configs = []

  for change in changes:
    config = validateChange(change)

    if config not in configs:
      configs.append(config)

  if not configs:
    raise ApplyError("no changes given")

  return configs

def parseStaged(output):
  return [line.strip() for line in output.splitlines() if line.strip()]

async def applyUci(session, settings, changes, dryRun = False):
  checkTier(WRITE, settings)

  configs = planConfigs(changes)

  async def stage():
    for change in changes:
      result = await session.run(change)

      if not result.ok:
        raise ApplyError(f"'{change}' failed: {result.stderr or 'non-zero exit'}")

  if dryRun:
    # stage, read back what uci says would change, then throw it away
    await stage()

    staged = parseStaged((await session.run("uci changes")).stdout)

    for config in configs:
      await session.run(buildRevert(config))

    return {
      "applied": False,
      "confirmed": False,
      "dryRun": True,
      "configs": configs,
      "staged": [redactUciOutput(line) for line in staged],
      "detail": "nothing was applied - this is what would change"
    }

  staged = []

  async def applyFn():
    nonlocal staged

    await stage()

    staged = parseStaged((await session.run("uci changes")).stdout)

    for config in configs:
      await session.run(buildCommit(config))

    for config in configs:
      reload = RELOAD_COMMANDS.get(config)

      if reload:
        await session.run(reload)

  risky = [config for config in configs if config in RISKY_CONFIGS]

  if risky:
    outcome = await withRollback(session, settings, risky, applyFn)
    detail = outcome.detail
    applied, confirmed = outcome.applied, outcome.confirmed
  else:
    # nothing here can strand us, so the watchdog would only add a restart
    await applyFn()
    applied, confirmed = True, True
    detail = f"applied and committed: {', '.join(configs)}"

  return {
    "applied": applied,
    "confirmed": confirmed,
    "dryRun": False,
    "configs": configs,
    "staged": [redactUciOutput(line) for line in staged],
    "detail": detail
  }

def registerTools(mcp, session, settings):
  @mcp.tool(annotations = annotationsFor(WRITE, "Apply UCI changes safely"))
  @toolSafe
  async def routerApplyUci(changes: list[str], dryRun: bool = False) -> ApplyReply:
    """Apply a list of `uci set|delete|add|rename` commands as one change.

    Works out which configs are affected, snapshots them, arms a rollback timer when
    the change could cut the connection, commits, reloads the right service, then
    verifies the router still answers. If it does not, the router restores itself.

    Pass dryRun=True to see exactly what would change without applying it.
    Use this rather than routerExec for configuration: routerExec has no safety net"""
    return await applyUci(session, settings, changes, dryRun = dryRun)
# ---------------------------------------------------------------------------- #
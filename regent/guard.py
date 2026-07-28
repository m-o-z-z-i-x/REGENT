# ---------------------------------------------------------------------------- #
# DESCRIPTION: risk tier enforcement for every tool the server exposes
# ---------------------------------------------------------------------------- #

# DEPENDENCIES --------------------------------------------------------------- #
from regent.logger import warning
from regent.metadata import envName
# ---------------------------------------------------------------------------- #

# LOGIC ---------------------------------------------------------------------- #
READ = "read"
WRITE = "write"
DESTRUCTIVE = "destructive"

KNOWN_TIERS = (READ, WRITE, DESTRUCTIVE)

# derived from the tier, so a tool cannot be gated one way and advertised another
TIER_HINTS = {
  READ: {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": True
  },
  WRITE: {
    "readOnlyHint": False,
    "destructiveHint": False,
    # routerExec can carry any command at all, so no write tool may claim idempotence
    "idempotentHint": False,
    "openWorldHint": True
  },
  DESTRUCTIVE: {
    "readOnlyHint": False,
    "destructiveHint": True,
    "idempotentHint": False,
    "openWorldHint": True
  }
}

class GuardError(Exception):
  pass

def annotationsFor(tier, title):
  if tier not in KNOWN_TIERS:
    raise GuardError(f"unknown risk tier '{tier}' - expected one of {', '.join(KNOWN_TIERS)}")

  return {"title": title, **TIER_HINTS[tier]}

def checkTier(tier, settings, confirmed = False):
  # the tier is passed in by the tool, never guessed from its name
  if tier not in KNOWN_TIERS:
    raise GuardError(f"unknown risk tier '{tier}' - expected one of {', '.join(KNOWN_TIERS)}")

  if tier == READ:
    return

  if not settings.writeEnabled:
    warning(f"refused a {tier} operation: write gate closed")

    raise GuardError(
      f"this is a {tier} operation and the write gate is closed - "
      f"set {envName('ENABLE_WRITE')}=1 to open it"
    )

  if tier == DESTRUCTIVE and not confirmed:
    warning("refused a destructive operation: not confirmed")

    raise GuardError(
      "this operation is destructive and needs explicit confirmation - "
      "pass confirm=True on the call to say you mean it"
    )
# ---------------------------------------------------------------------------- #
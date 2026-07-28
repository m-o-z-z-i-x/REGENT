# ---------------------------------------------------------------------------- #
# DESCRIPTION: pure builders and parsers for the uci configuration interface
# ---------------------------------------------------------------------------- #

# DEPENDENCIES --------------------------------------------------------------- #
from shlex import split as shellSplit
# ---------------------------------------------------------------------------- #

# LOGIC ---------------------------------------------------------------------- #
class UciError(Exception):
  pass

def quoteValue(value):
  # single-quote for the router's shell, escaping any quote inside the value
  escaped = str(value).replace("'", "'\\''")

  return f"'{escaped}'"

def requireSectionPath(pathExpr):
  # "network" alone is a config, not a target: writing to it silently does nothing
  if "." not in pathExpr:
    raise UciError(f"'{pathExpr}' is a config name, not a section path - expected something like 'network.lan.proto'")

  return pathExpr

def buildShow(config):
  return f"uci show {config}"

def buildGet(pathExpr):
  return f"uci get {requireSectionPath(pathExpr)}"

def buildSet(pathExpr, value):
  return f"uci set {requireSectionPath(pathExpr)}={quoteValue(value)}"

def buildDelete(pathExpr):
  return f"uci delete {requireSectionPath(pathExpr)}"

def buildCommit(config = None):
  return f"uci commit {config}" if config else "uci commit"

def buildRevert(config = None):
  return f"uci revert {config}" if config else "uci revert"

def parseValue(raw):
  # uci renders lists as several quoted words on one line; a single word stays a string
  parts = shellSplit(raw)

  return parts[0] if len(parts) == 1 else parts

def parseShow(output):
  # turn uci show output into {section: {".type": type, option: value}}
  sections = {}

  for line in output.splitlines():
    line = line.strip()

    if not line or "=" not in line:
      continue

    key, _, raw = line.partition("=")
    parts = key.split(".")

    if len(parts) == 2:
      sections.setdefault(parts[1], {})[".type"] = parseValue(raw)
    elif len(parts) == 3:
      sections.setdefault(parts[1], {})[parts[2]] = parseValue(raw)

  return sections
# ---------------------------------------------------------------------------- #
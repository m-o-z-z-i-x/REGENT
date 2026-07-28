# ---------------------------------------------------------------------------- #
# DESCRIPTION: validate anything that reaches the router's shell
# ---------------------------------------------------------------------------- #

# DEPENDENCIES --------------------------------------------------------------- #
import re
# ---------------------------------------------------------------------------- #

# LOGIC ---------------------------------------------------------------------- #
# a uci config, section or interface name: no slashes, spaces, or shell punctuation
#
# uci writes anonymous sections as firewall.@zone[0], so @ and [N] have to be allowed
SAFE_NAME = re.compile(r"^@?[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}(\[\d{1,3}\])?$")

# absolute paths only, since a relative one can climb out of where it was meant to stay
SAFE_PATH = re.compile(r"^/[A-Za-z0-9_./-]{0,255}$")

# a hostname or address handed to ping, nslookup, curl
SAFE_HOST = re.compile(r"^[A-Za-z0-9._:-]{1,253}$")

# listed explicitly so a refusal can name the character it found
METACHARACTERS = (";", "|", "&", "$", "`", "\\", "\n", "\r", "<", ">", "(", ")", "{", "}", "*", "?", "!", "#", "'", '"')

class UnsafeValue(Exception):
  pass

def findMetacharacter(value):
  for token in METACHARACTERS:
    if token in str(value):
      return token

  return None

def requireSafeName(value, kind = "name"):
  # for identifiers put into a command directly: uci reads them as a path, so they cannot be quoted
  text = str(value or "").strip()

  if not SAFE_NAME.match(text):
    found = findMetacharacter(text)
    detail = f"it contains '{found}'" if found else "it is empty or has an unexpected shape"

    raise UnsafeValue(f"'{text[:40]}' is not a usable {kind} - {detail}")

  return text

def requireSafePath(value):
  text = str(value or "").strip()

  if not SAFE_PATH.match(text):
    found = findMetacharacter(text)
    detail = f"it contains '{found}'" if found else "it must be an absolute path"

    raise UnsafeValue(f"'{text[:60]}' is not a usable path - {detail}")

  if ".." in text:
    raise UnsafeValue(f"'{text[:60]}' is not a usable path - it walks upwards with '..'")

  return text

def requireSafeHost(value):
  text = str(value or "").strip()

  if not SAFE_HOST.match(text):
    found = findMetacharacter(text)
    detail = f"it contains '{found}'" if found else "it is empty or too long"

    raise UnsafeValue(f"'{text[:60]}' is not a plain hostname or address - {detail}")

  return text

def quoteArgument(value):
  # single-quote a value for the router's shell. identifiers go through requireSafeName instead
  escaped = str(value).replace("'", "'\\''")

  return f"'{escaped}'"
# ---------------------------------------------------------------------------- #
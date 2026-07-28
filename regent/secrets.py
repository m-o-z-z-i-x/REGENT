# ---------------------------------------------------------------------------- #
# DESCRIPTION: strip credentials out of anything the router sends back
# ---------------------------------------------------------------------------- #

# DEPENDENCIES --------------------------------------------------------------- #
import re
# ---------------------------------------------------------------------------- #

# LOGIC ---------------------------------------------------------------------- #
MASK = "***redacted***"

# option names whose values are credentials, matched against the last segment of a uci path
#
# deliberately broad: hiding a readable field costs less than leaking a credential
SECRET_NAMES = (
  "key", "psk", "wpa_psk", "password", "passwd", "pass",
  "secret", "auth_secret", "token", "api_key", "apikey",
  "uuid", "private_key", "privatekey", "private_key_file",
  "reality_privatekey", "reality_publickey", "reality_shortid", "reality_spiderx",
  "public_key", "publickey", "preshared_key", "credential",
)

# "url" is harmless as an adblock source but carries a token in a subscription, so match the path
SECRET_PATHS = (
  re.compile(r"subscribe.*\.url$", re.IGNORECASE),
  re.compile(r"subscription.*\.url$", re.IGNORECASE),
  re.compile(r"\.sub(scribe)?_?url$", re.IGNORECASE),
)

# options that are only credentials inside certain kinds of section
CONTEXT_SECRET_NAMES = ("url", "address")

# sections whose contents are account credentials
SECRET_SECTION_TYPES = ("subscribe_list", "subscription", "subscribe")

# uci show declares a section as "config.section=type" before listing its options
UCI_SECTION = re.compile(r"^([\w.@\[\]-]+)=([\w-]+)$")

# and then each option as "config.section.option=value"
UCI_OPTION = re.compile(r"^([\w.@\[\]-]+)\.([\w-]+)=(.*)$")

def isSecretName(name):
  return name.strip().lower() in SECRET_NAMES

def isSecretPath(path):
  return any(pattern.search(path) for pattern in SECRET_PATHS)

def redactUciOutput(text):
  # whether a field is secret depends on its section type, which uci declares on an earlier line
  sectionTypes = {}
  output = []

  for line in text.splitlines():
    section = UCI_SECTION.match(line)

    if section:
      sectionTypes[section.group(1)] = section.group(2)
      output.append(line)

      continue

    option = UCI_OPTION.match(line)

    if not option:
      output.append(line)

      continue

    path, name, _ = option.groups()
    inSecretSection = sectionTypes.get(path) in SECRET_SECTION_TYPES

    secret = (
      isSecretName(name)
      or isSecretPath(f"{path}.{name}")
      or (name.lower() in CONTEXT_SECRET_NAMES and inSecretSection)
    )

    output.append(f"{path}.{name}={MASK}" if secret else line)

  return "\n".join(output)

def redactMapping(mapping):
  # for parsed structures rather than raw output
  return {
    key: (MASK if isSecretName(key) else value)
    for key, value in mapping.items()
  }

# the same secrets written as a command rather than read back, which is what the audit log holds
UCI_ASSIGNMENT = re.compile(r"((?:^|\s)uci\s+(?:set|add_list|del_list)\s+[\w.@\[\]-]+\.)([\w-]+)=(\S+)")

def redactCommand(command):
  # the audit log outlives the session, so a password set through it must not be stored
  def replace(match):
    prefix, option, _ = match.groups()

    return f"{prefix}{option}={MASK}" if isSecretName(option) else match.group(0)

  return UCI_ASSIGNMENT.sub(replace, str(command))

def countRedactions(text):
  return text.count(MASK)
# ---------------------------------------------------------------------------- #
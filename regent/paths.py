# ---------------------------------------------------------------------------- #
# DESCRIPTION: where the key, the log and .env live, checked out or installed
# ---------------------------------------------------------------------------- #

# DEPENDENCIES --------------------------------------------------------------- #
from os import getenv, name as osName, path

from regent.metadata import SLUG
# ---------------------------------------------------------------------------- #

# LOGIC ---------------------------------------------------------------------- #
# a checkout keeps these beside the code. an installed copy has no project directory
PROJECT_KEY_PATH = "./keys/key"
PROJECT_LOG_PATH = "./logs/execution-report.log"

def configDir():
  # %APPDATA% on windows, the XDG location elsewhere
  if osName == "nt":
    base = getenv("APPDATA")
  else:
    base = getenv("XDG_CONFIG_HOME") or path.join(path.expanduser("~"), ".config")

  return path.join(base or path.expanduser("~"), SLUG)

def resolve(projectRelative, installedName):
  # the checkout wins when it exists, so development does not read an installed copy's files
  local = path.abspath(projectRelative)

  return local if path.exists(local) else path.join(configDir(), installedName)

def keyPath():
  return resolve(PROJECT_KEY_PATH, "key")

def logPath():
  # the log is created rather than found, so test the directory instead of the file
  return (
    path.abspath(PROJECT_LOG_PATH)
    if path.isdir(path.dirname(path.abspath(PROJECT_LOG_PATH)))
    else path.join(configDir(), "logs", "execution-report.log")
  )

def envFiles():
  # both are read, nearest first. dotenv keeps what is already set, so an environment variable wins
  return [path.abspath(".env"), path.join(configDir(), ".env")]
# ---------------------------------------------------------------------------- #
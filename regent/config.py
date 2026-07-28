# ---------------------------------------------------------------------------- #
# DESCRIPTION: runtime settings loaded from the environment and .env
# ---------------------------------------------------------------------------- #

# DEPENDENCIES --------------------------------------------------------------- #
from os import getenv
from dataclasses import dataclass

from dotenv import load_dotenv

from regent.metadata import envName
from regent.paths import keyPath, envFiles
# ---------------------------------------------------------------------------- #

# LOGIC ---------------------------------------------------------------------- #
# these do not vary between routers, so they live here. an environment variable still overrides each
DEFAULT_USER = "root"
DEFAULT_PORT = 22

# what a stock OpenWrt answers on, shown in the message that asks for an address
EXAMPLE_HOST = "192.168.1.1"

# enough for opkg over a slow uplink, little enough that a hung command is noticed
DEFAULT_TIMEOUT = 30

# long enough for an interface to come back, short enough that a mistake is not a walk to the router
DEFAULT_ROLLBACK_DELAY = 90

class ConfigError(Exception):
  pass

@dataclass
class Settings:
  host: str
  user: str
  port: int
  keyPath: str
  timeout: int
  rollbackDelay: int
  writeEnabled: bool

def readInt(name, fallback):
  # treat an unparseable value as absent rather than crashing at import time
  raw = getenv(name)

  if not raw:
    return fallback

  try:
    return int(raw)
  except ValueError:
    return fallback

def loadSettings():
  # nearest file first. dotenv keeps what is already set, so an environment variable always wins
  for envFile in envFiles():
    load_dotenv(envFile)

  host = getenv(envName("HOST"))

  if not host:
    # .env.example ships only in the sdist, so spell the settings out instead of naming it
    raise ConfigError(
      f"{envName('HOST')} is not set. Create {envFiles()[-1]} containing:\n"
      f"  {envName('HOST')}={EXAMPLE_HOST}\n"
      f"  {envName('USER')}={DEFAULT_USER}\n"
      f"  {envName('PORT')}={DEFAULT_PORT}\n"
      f"and put the ssh key beside it, named 'key'"
    )

  return Settings(
    host = host,
    user = getenv(envName("USER")) or DEFAULT_USER,
    port = readInt(envName("PORT"), DEFAULT_PORT),
    keyPath = getenv(envName("KEY")) or keyPath(),
    timeout = readInt(envName("TIMEOUT"), DEFAULT_TIMEOUT),
    rollbackDelay = readInt(envName("ROLLBACK_DELAY"), DEFAULT_ROLLBACK_DELAY),
    writeEnabled = getenv(envName("ENABLE_WRITE")) == "1"
  )
# ---------------------------------------------------------------------------- #
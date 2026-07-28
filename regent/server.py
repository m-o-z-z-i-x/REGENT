# -*- coding: utf-8 -*-

# DEPENDENCIES --------------------------------------------------------------- #
from sys import stderr

from fastmcp import FastMCP

from regent import system, network, wireless, firewall, packages, diagnostics, topology, apply, adblock, vpn, bridge, backup, services
from regent.ssh import RouterSession
from regent.config import loadSettings, ConfigError
from regent.logger import exception
from regent.metadata import APP_NAME, APP_VERSION, DESCRIPTION
# ---------------------------------------------------------------------------- #

# LOGIC ---------------------------------------------------------------------- #
def buildServer(settings):
  # pass our version explicitly, or fastmcp reports its own to the client
  mcp = FastMCP(APP_NAME, version = APP_VERSION, instructions = DESCRIPTION)
  session = RouterSession(settings)

  # every domain shares the one session - dropbear caps concurrent connections hard
  for domain in (system, network, wireless, firewall, packages, diagnostics, topology, apply, adblock, vpn, bridge, backup, services):
    domain.registerTools(mcp, session, settings)

  return mcp

def main():
  # stdout carries the json-rpc stream, so diagnostics go to stderr
  try:
    settings = loadSettings()
  except ConfigError as err:
    print(f"{APP_NAME} {APP_VERSION}: {err}", file = stderr)

    raise SystemExit(1)

  gate = "read+write" if settings.writeEnabled else "read-only"

  print(f"{APP_NAME} {APP_VERSION} -> {settings.user}@{settings.host}:{settings.port} [{gate}]", file = stderr)

  try:
    buildServer(settings).run()
  except Exception as err:
    exception(err)

    raise

if __name__ == "__main__":
  main()
# ---------------------------------------------------------------------------- #
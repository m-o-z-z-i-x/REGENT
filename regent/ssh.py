# ---------------------------------------------------------------------------- #
# DESCRIPTION: authenticated ssh session to the router, reused across tool calls
# ---------------------------------------------------------------------------- #

# DEPENDENCIES --------------------------------------------------------------- #
from os import path
from dataclasses import dataclass

import asyncssh

from regent.logger import audit, exception
from regent.metadata import envName
# ---------------------------------------------------------------------------- #

# LOGIC ---------------------------------------------------------------------- #
class SshError(Exception):
  pass

@dataclass
class CommandResult:
  exitCode: int
  stdout: str
  stderr: str

  @property
  def ok(self):
    return self.exitCode == 0

class RouterSession:
  # one connection for the whole process: dropbear caps concurrent sessions hard.
  #
  # GSSAPI is disabled: dropbear never offers it, and on windows the attempt itself can fail
  def __init__(self, settings):
    self.settings = settings
    self.connection = None

  def resolveKeyPath(self):
    keyPath = path.abspath(self.settings.keyPath)

    if not path.isfile(keyPath):
      raise SshError(f"ssh key not found at {keyPath} - check {envName('KEY')}")

    return keyPath

  async def connect(self):
    if self.connection is not None:
      return

    keyPath = self.resolveKeyPath()

    try:
      self.connection = await asyncssh.connect(
        self.settings.host,
        port = self.settings.port,
        username = self.settings.user,
        client_keys = [keyPath],
        known_hosts = None,
        gss_host = None
      )
    except asyncssh.PermissionDenied as err:
      raise SshError(f"router refused the key - append keys/key.pub to /etc/dropbear/authorized_keys on {self.settings.host}") from err
    except PermissionError as err:
      # the local machine refused the socket, so the router was never contacted
      raise SshError(
        f"this machine blocked the connection to {self.settings.host}:{self.settings.port} - {err}. "
        f"the router was never contacted, so check the local firewall for the running interpreter, "
        f"not the network"
      ) from err
    except (OSError, asyncssh.Error) as err:
      raise SshError(f"cannot reach {self.settings.host}:{self.settings.port} - {err}") from err

  async def run(self, command):
    await self.connect()

    try:
      completed = await self.connection.run(command, check = False, timeout = self.settings.timeout)
    except TimeoutError as err:
      audit(command, -1)

      raise SshError(f"command timed out after {self.settings.timeout}s: {command}") from err
    except (OSError, asyncssh.Error) as err:
      audit(command, -1)
      exception(err)

      raise SshError(f"connection lost while running: {command}") from err

    result = CommandResult(
      exitCode = int(completed.exit_status or 0),
      stdout = str(completed.stdout or "").strip(),
      stderr = str(completed.stderr or "").strip()
    )

    audit(command, result.exitCode)

    return result

  async def close(self):
    if self.connection is not None:
      self.connection.close()

      await self.connection.wait_closed()

      self.connection = None
# ---------------------------------------------------------------------------- #
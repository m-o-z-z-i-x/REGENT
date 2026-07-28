from regent.ssh import CommandResult, SshError

class FakeSession:
  # stands in for RouterSession. `responses` maps a command to a result, `default` answers
  # the rest, and every command is recorded so tests can check the order
  def __init__(self, responses = None, default = None, failOn = None):
    self.responses = responses or {}
    self.default = default or CommandResult(exitCode = 0, stdout = "", stderr = "")
    self.failOn = failOn
    self.commands = []
    self.connected = False
    self.closed = False

  async def connect(self):
    self.connected = True

  async def run(self, command):
    self.commands.append(command)

    if self.failOn and self.failOn in command:
      raise SshError(f"connection lost while running: {command}")

    return self.responses.get(command, self.default)

  async def close(self):
    self.closed = True
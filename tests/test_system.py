import pytest

from regent.system import getSystemInfo, execCommand, rebootDevice
from regent.guard import GuardError
from regent.ssh import CommandResult
from regent.config import Settings
from regent.ubus import buildCall
from tests.fakes import FakeSession

def makeSettings(writeEnabled = False):
  return Settings(
    host = "192.168.1.1",
    user = "root",
    port = 22,
    keyPath = "./keys/key",
    timeout = 30,
    rollbackDelay = 90,
    writeEnabled = writeEnabled
  )

async def test_getSystemInfoMergesBoardAndInfo():
  session = FakeSession(responses = {
    buildCall("system", "board"): CommandResult(
      exitCode = 0,
      stdout = '{"model": "GL-MT300N", "release": {"version": "23.05.2"}}',
      stderr = ""
    ),
    buildCall("system", "info"): CommandResult(
      exitCode = 0,
      stdout = '{"uptime": 4242, "memory": {"free": 12345}}',
      stderr = ""
    )
  })

  info = await getSystemInfo(session, makeSettings())

  assert info["model"] == "GL-MT300N"
  assert info["release"]["version"] == "23.05.2"
  assert info["uptime"] == 4242
  assert info["memory"]["free"] == 12345

async def test_execCommandIsRefusedWhenTheGateIsClosed():
  session = FakeSession()

  with pytest.raises(GuardError):
    await execCommand(session, makeSettings(writeEnabled = False), "ls /etc")

  assert session.commands == []

async def test_execCommandRunsWhenTheGateIsOpen():
  session = FakeSession(responses = {
    "ls /etc": CommandResult(exitCode = 0, stdout = "config\npasswd", stderr = "")
  })

  result = await execCommand(session, makeSettings(writeEnabled = True), "ls /etc")

  assert result["exitCode"] == 0
  assert result["stdout"] == "config\npasswd"
  assert result["redacted"] == 0
  assert session.commands == ["ls /etc"]

async def test_execCommandRedactsCredentialsItHappensToRead():
  # this tool runs whatever it is given, so the filter has to sit on the output
  session = FakeSession(responses = {
    "uci show passwall": CommandResult(
      exitCode = 0,
      stdout = "passwall.n1.address='example.com'\npasswall.n1.uuid='00000000-0000-4000-8000-000000000000'",
      stderr = ""
    )
  })

  result = await execCommand(session, makeSettings(writeEnabled = True), "uci show passwall")

  assert "00000000-0000-4000-8000-000000000000" not in result["stdout"]
  assert "example.com" in result["stdout"]
  assert result["redacted"] == 1

async def test_rebootIsRefusedWithoutConfirmation():
  session = FakeSession()

  with pytest.raises(GuardError) as err:
    await rebootDevice(session, makeSettings(writeEnabled = True), confirm = False)

  assert "confirm" in str(err.value).lower()
  assert session.commands == []

async def test_rebootRunsWithGateAndConfirmation():
  session = FakeSession()

  result = await rebootDevice(session, makeSettings(writeEnabled = True), confirm = True)

  assert session.commands == ["reboot"]
  assert result["rebooting"] is True
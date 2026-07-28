import pytest

from regent.services import (
  requireAction, parseServices, buildAction, listServices, controlService,
  ServiceError, ACTIONS, SELF_HARMING, DETACH
)
from regent.guard import GuardError
from regent.shell import UnsafeValue
from regent.ssh import CommandResult
from regent.config import Settings
from tests.fakes import FakeSession

def makeSettings(writeEnabled = True):
  return Settings(
    host = "192.168.10.1", user = "root", port = 22, keyPath = "./keys/key",
    timeout = 30, rollbackDelay = 90, writeEnabled = writeEnabled
  )

@pytest.mark.parametrize("action", ACTIONS)
def test_everyDocumentedActionIsAccepted(action):
  assert requireAction(action) == action

def test_anUnknownActionIsRefusedWithTheChoices():
  with pytest.raises(ServiceError) as err:
    requireAction("obliterate")

  assert "restart" in str(err.value)

def test_parseServicesMergesTheThreeListings():
  services = parseServices("dnsmasq firewall network", "dnsmasq network", "dnsmasq")
  byName = {entry["name"]: entry for entry in services}

  assert byName["dnsmasq"] == {"name": "dnsmasq", "autostart": True, "running": True}
  assert byName["firewall"] == {"name": "firewall", "autostart": False, "running": False}
  assert byName["network"]["autostart"] is True
  assert byName["network"]["running"] is False

def test_parseServicesSurvivesEmptyInput():
  assert parseServices("", "", "") == []

def test_anOrdinaryServiceRunsInTheForeground():
  assert buildAction("dnsmasq", "restart") == "/etc/init.d/dnsmasq restart 2>&1"

@pytest.mark.parametrize("name", DETACH)
def test_servicesThatRebuildTheFirewallAreDetached(name):
  # restarting these drops the connection carrying the command, so they must be detached
  assert "nohup" in buildAction(name, "restart")

def test_startingADetachedServiceStillRunsInTheForeground():
  # only stop, restart and reload tear the ruleset down
  assert "nohup" not in buildAction("network", "start")

async def test_controlIsRefusedWithTheGateShut():
  session = FakeSession()

  with pytest.raises(GuardError):
    await controlService(session, makeSettings(writeEnabled = False), "dnsmasq", "restart")

  assert session.commands == []

@pytest.mark.parametrize("hostile", ["dnsmasq; reboot", "$(reboot)", "../../bin/sh"])
async def test_serviceNamesCannotCarryACommand(hostile):
  session = FakeSession()

  with pytest.raises(UnsafeValue):
    await controlService(session, makeSettings(), hostile, "restart")

  assert session.commands == []

async def test_anUnknownServiceIsRefusedBeforeAnythingRuns():
  session = FakeSession(default = CommandResult(exitCode = 0, stdout = "no", stderr = ""))

  with pytest.raises(ServiceError) as err:
    await controlService(session, makeSettings(), "nosuchthing", "restart")

  assert "no service called" in str(err.value)

@pytest.mark.parametrize("name", sorted(SELF_HARMING))
async def test_stoppingAWayBackInNeedsConfirmation(name):
  session = FakeSession(responses = {
    f"test -x /etc/init.d/{name} && echo yes || echo no": CommandResult(exitCode = 0, stdout = "yes", stderr = "")
  })

  with pytest.raises(ServiceError) as err:
    await controlService(session, makeSettings(), name, "stop")

  assert "confirm=True" in str(err.value)
  assert not any(name in command and "stop" in command for command in session.commands)

async def test_theRefusalExplainsWhatWouldBeLost():
  session = FakeSession(responses = {
    "test -x /etc/init.d/dropbear && echo yes || echo no": CommandResult(exitCode = 0, stdout = "yes", stderr = "")
  })

  with pytest.raises(ServiceError) as err:
    await controlService(session, makeSettings(), "dropbear", "stop")

  assert "ssh server" in str(err.value)

async def test_confirmingLetsItThrough():
  session = FakeSession(responses = {
    "test -x /etc/init.d/dropbear && echo yes || echo no": CommandResult(exitCode = 0, stdout = "yes", stderr = "")
  })

  result = await controlService(session, makeSettings(), "dropbear", "stop", confirm = True)

  assert result["succeeded"] is True

async def test_restartingASafeServiceNeedsNoConfirmation():
  session = FakeSession(responses = {
    "test -x /etc/init.d/dnsmasq && echo yes || echo no": CommandResult(exitCode = 0, stdout = "yes", stderr = "")
  })

  result = await controlService(session, makeSettings(), "dnsmasq", "restart")

  assert result["action"] == "restart"
  assert "/etc/init.d/dnsmasq restart 2>&1" in session.commands

async def test_listingReportsWhatIsRunning():
  session = FakeSession(responses = {
    "ls /etc/init.d/ 2>/dev/null": CommandResult(exitCode = 0, stdout = "dnsmasq network", stderr = ""),
  }, default = CommandResult(exitCode = 0, stdout = "dnsmasq", stderr = ""))

  result = await listServices(session, makeSettings())

  assert result["count"] == 2
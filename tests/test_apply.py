import pytest

from regent.apply import (
  validateChange, planConfigs, parseStaged, applyUci, ApplyError,
  RELOAD_COMMANDS, RISKY_CONFIGS
)

from regent.guard import GuardError
from regent.ssh import CommandResult
from regent.config import Settings
from regent.rollback import buildArm, buildDisarm, buildProbe
from tests.fakes import FakeSession

def makeSettings(writeEnabled = True):
  return Settings(
    host = "192.168.10.1", user = "root", port = 22, keyPath = "./keys/key",
    timeout = 30, rollbackDelay = 90, writeEnabled = writeEnabled
  )

def test_validateChangeReadsTheConfigName():
  assert validateChange("uci set network.lan.ipaddr='192.168.10.1'") == "network"

def test_validateChangeHandlesDelete():
  assert validateChange("uci delete network.wwan2") == "network"

@pytest.mark.parametrize("hostile", [
  "uci set network.lan.ipaddr='1.2.3.4'; reboot",
  "uci set network.lan.ipaddr=$(reboot)",
  "uci set a.b.c=`reboot`",
  "uci set a.b.c=1 && rm -rf /",
  "uci set a.b.c=1 | tee /etc/passwd",
  "uci set a.b.c=1 > /etc/shadow",
])

def test_validateChangeRefusesASecondCommandOnTheLine(hostile):
  # these strings reach a shell, so anything unchecked here is remote code execution
  with pytest.raises(ApplyError):
    validateChange(hostile)

def test_validateChangeRefusesNonUciCommands():
  with pytest.raises(ApplyError) as err:
    validateChange("reboot")

  assert "routerExec" in str(err.value)

def test_validateChangeRefusesUciActionsThatDoNotStage():
  # `uci commit` and `uci revert` are the caller's job, not the payload's
  with pytest.raises(ApplyError):
    validateChange("uci commit network")

def test_planConfigsDeduplicatesAndKeepsOrder():
  configs = planConfigs([
    "uci set network.lan.ipaddr='192.168.10.1'",
    "uci set firewall.@zone[0].name='lan'",
    "uci set network.lan.netmask='255.255.255.0'"
  ])

  assert configs == ["network", "firewall"]

def test_planConfigsRefusesAnEmptyList():
  with pytest.raises(ApplyError):
    planConfigs([])

def test_parseStagedDropsBlankLines():
  assert parseStaged("network.lan.ipaddr='10.0.0.1'\n\n") == ["network.lan.ipaddr='10.0.0.1'"]

def test_aServiceConfigChangeTriggersItsReload():
  # committing alone left the daemon on its old config, so the change looked applied and was not
  assert "passwall2" in RELOAD_COMMANDS
  assert "adblock" in RELOAD_COMMANDS

def test_passwallReloadIsDetachedFromTheSession():
  # restarting it drops the ssh connection carrying the command, so it has to be detached
  assert "nohup" in RELOAD_COMMANDS["passwall2"]

async def test_changingPasswallRestartsIt():
  session = FakeSession()

  await applyUci(session, makeSettings(), ["uci set passwall2.cfg1.enabled='1'"])

  assert "uci commit passwall2" in session.commands
  assert RELOAD_COMMANDS["passwall2"] in session.commands

def test_everyRiskyConfigHasAReloadCommand():
  # committing without reloading leaves the daemon on the old config
  assert all(config in RELOAD_COMMANDS for config in RISKY_CONFIGS)

async def test_applyIsRefusedWhenTheWriteGateIsClosed():
  session = FakeSession()

  with pytest.raises(GuardError):
    await applyUci(session, makeSettings(writeEnabled = False), ["uci set network.lan.ipaddr='10.0.0.1'"])

  assert session.commands == []

async def test_dryRunStagesReadsBackAndRevertsWithoutCommitting():
  session = FakeSession(responses = {
    "uci changes": CommandResult(exitCode = 0, stdout = "network.lan.ipaddr='10.0.0.1'", stderr = "")
  })

  result = await applyUci(session, makeSettings(), ["uci set network.lan.ipaddr='10.0.0.1'"], dryRun = True)

  assert result["applied"] is False
  assert result["staged"] == ["network.lan.ipaddr='10.0.0.1'"]
  assert "uci revert network" in session.commands
  assert not any(command.startswith("uci commit") for command in session.commands)

async def test_dryRunNeverArmsTheWatchdog():
  session = FakeSession()

  await applyUci(session, makeSettings(), ["uci set network.lan.ipaddr='10.0.0.1'"], dryRun = True)

  assert not any("nohup" in command for command in session.commands)

async def test_aRiskyChangeRunsUnderTheWatchdogAndReloads():
  session = FakeSession(responses = {
    buildProbe(): CommandResult(exitCode = 0, stdout = "alive", stderr = "")
  })

  result = await applyUci(session, makeSettings(), ["uci set network.lan.ipaddr='10.0.0.1'"])

  assert result["applied"] is True
  assert result["confirmed"] is True
  assert session.commands[0] == buildArm(90, ["network"])
  assert "uci commit network" in session.commands
  assert RELOAD_COMMANDS["network"] in session.commands
  assert session.commands[-1] == buildDisarm()

async def test_aHarmlessConfigSkipsTheWatchdog():
  # the system config cannot cut anyone off, so insuring it would cost more than it protects
  session = FakeSession()

  result = await applyUci(session, makeSettings(), ["uci set system.@system[0].zonename='Asia/Tokyo'"])

  assert result["confirmed"] is True
  assert not any("nohup" in command for command in session.commands)
  assert RELOAD_COMMANDS["system"] in session.commands

async def test_aFailedChangeStopsBeforeCommitting():
  session = FakeSession(default = CommandResult(exitCode = 1, stdout = "", stderr = "invalid"))

  result = await applyUci(session, makeSettings(), ["uci set network.lan.ipaddr='nonsense'"])

  assert result["applied"] is False
  assert not any(command.startswith("uci commit") for command in session.commands)

async def test_stagedOutputIsRedacted():
  session = FakeSession(responses = {
    "uci changes": CommandResult(
      exitCode = 0,
      stdout = "wireless.wifinet1.key='hunter2'",
      stderr = ""
    ),
    buildProbe(): CommandResult(exitCode = 0, stdout = "alive", stderr = "")
  })

  result = await applyUci(session, makeSettings(), ["uci set wireless.wifinet1.key='hunter2'"])

  assert "hunter2" not in " ".join(result["staged"])
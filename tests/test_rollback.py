import pytest

from regent.rollback import buildArm, buildDisarm, buildProbe, withRollback, PID_FILE, BACKUP_DIR
from regent.ssh import CommandResult, SshError
from regent.config import Settings
from tests.fakes import FakeSession

def makeSettings():
  return Settings(
    host = "192.168.1.1",
    user = "root",
    port = 22,
    keyPath = "./keys/key",
    timeout = 30,
    rollbackDelay = 90,
    writeEnabled = True
  )

def test_buildArmDetachesFromTheSession():
  command = buildArm(90, ["network", "firewall"])

  assert "nohup" in command
  assert "sleep 90" in command
  assert PID_FILE in command

def test_buildArmSnapshotsEachConfigBeforeTheChange():
  command = buildArm(90, ["network", "firewall"])

  assert f"cp /etc/config/network {BACKUP_DIR}/network" in command
  assert f"cp /etc/config/firewall {BACKUP_DIR}/firewall" in command

def test_buildArmRestoresFromTheSnapshotNotUciRevert():
  # uci revert only undoes staged changes, so it protects nothing once a change is committed
  command = buildArm(90, ["network"])

  assert "uci revert" not in command
  assert f"cp {BACKUP_DIR}/network /etc/config/network" in command

def test_buildArmRestartsNetworkingSoTheRestoreTakesEffect():
  assert "/etc/init.d/network restart" in buildArm(90, ["network"])

def test_buildDisarmKillsThePidAndClearsTheSnapshots():
  command = buildDisarm()

  assert "kill" in command
  assert PID_FILE in command
  assert BACKUP_DIR in command

def test_buildProbeIsCheap():
  assert buildProbe() == "echo alive"

async def test_withRollbackDisarmsWhenTheChangeIsConfirmed():
  session = FakeSession(responses = {buildProbe(): CommandResult(exitCode = 0, stdout = "alive", stderr = "")})

  async def applyFn():
    await session.run("uci set network.wan.proto='dhcp'")

  outcome = await withRollback(session, makeSettings(), ["network"], applyFn)

  assert outcome.applied is True
  assert outcome.confirmed is True
  assert session.commands[0] == buildArm(90, ["network"])
  assert session.commands[-1] == buildDisarm()

async def test_withRollbackLeavesTheTimerArmedWhenTheProbeFails():
  session = FakeSession(
    responses = {buildProbe(): CommandResult(exitCode = 1, stdout = "", stderr = "")}
  )

  async def applyFn():
    await session.run("uci set network.wan.proto='dhcp'")

  outcome = await withRollback(session, makeSettings(), ["network"], applyFn)

  assert outcome.applied is True
  assert outcome.confirmed is False
  assert buildDisarm() not in session.commands

async def test_withRollbackLeavesTheTimerArmedWhenTheSessionDies():
  session = FakeSession(failOn = "uci set")

  async def applyFn():
    await session.run("uci set network.wan.proto='dhcp'")

  outcome = await withRollback(session, makeSettings(), ["network"], applyFn)

  assert outcome.confirmed is False
  assert buildDisarm() not in session.commands

async def test_withRollbackRefusesToApplyWhenArmingFails():
  session = FakeSession(default = CommandResult(exitCode = 1, stdout = "", stderr = "no space"))
  applied = []

  async def applyFn():
    applied.append(True)

  outcome = await withRollback(session, makeSettings(), ["network"], applyFn)

  assert outcome.applied is False
  assert applied == []
  assert "arm" in outcome.detail.lower()
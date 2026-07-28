import pytest

from regent.adblock import moveWorkingStorage
from regent.bridge import planShareUplink
from regent.diagnostics import getSystemLog, pingHost, TargetError
from regent.secrets import redactCommand, MASK
from regent.shell import UnsafeValue
from regent.logger import audit
from regent.config import Settings
from regent.ssh import CommandResult
from tests.fakes import FakeSession
from tests.test_bridge import BROKEN

INJECTIONS = ["; reboot", " && rm -rf /", "$(reboot)", "`reboot`", " | sh"]

def makeSettings(writeEnabled = True):
  return Settings(
    host = "192.168.10.1", user = "root", port = 22, keyPath = "./keys/key",
    timeout = 30, rollbackDelay = 90, writeEnabled = writeEnabled
  )

# ---------------------------------------------------------------------------- #
# 1. routerAdblockUseStorage put its argument straight into mkdir
# ---------------------------------------------------------------------------- #

@pytest.mark.parametrize("payload", INJECTIONS)
async def test_adblockStoragePathCannotCarryACommand(payload):
  session = FakeSession()

  with pytest.raises(UnsafeValue):
    await moveWorkingStorage(session, makeSettings(), f"/overlay{payload}")

  assert session.commands == []

async def test_adblockStorageRefusesARelativePath():
  session = FakeSession()

  with pytest.raises(UnsafeValue):
    await moveWorkingStorage(session, makeSettings(), "overlay/adblock")

  assert session.commands == []

# ---------------------------------------------------------------------------- #
# 2. routerShareUplink interpolated its argument into uci paths
# ---------------------------------------------------------------------------- #

@pytest.mark.parametrize("payload", INJECTIONS)
def test_shareUplinkInterfaceNameCannotCarryACommand(payload):
  with pytest.raises(UnsafeValue):
    planShareUplink(**dict(BROKEN, lanName = f"lan{payload}"))

def test_shareUplinkStillAcceptsOrdinaryNames():
  plan = planShareUplink(**dict(BROKEN, lanName = "lan"))

  assert plan["changes"]

# ---------------------------------------------------------------------------- #
# 3. the audit log recorded whole commands, passwords included
# ---------------------------------------------------------------------------- #

def test_aPasswordSetOverUciNeverReachesTheLog():
  redacted = redactCommand("uci set wireless.wifinet1.key='hunter2'")

  assert "hunter2" not in redacted
  assert f"wireless.wifinet1.key={MASK}" in redacted

def test_theCommandShapeStaysReadable():
  # the log is useless if the redaction eats the command as well as the secret
  redacted = redactCommand("uci set network.lan.ipaddr='192.168.10.1'")

  assert redacted == "uci set network.lan.ipaddr='192.168.10.1'"

def test_auditWritesTheRedactedForm(tmp_path, monkeypatch):
  from regent import logger

  written = []
  monkeypatch.setattr(logger._logger, "info", lambda message: written.append(message))

  audit("uci set passwall2.node1.uuid='00000000-0000-4000-8000-000000000000'", 0)

  assert "00000000-0000-4000-8000-000000000000" not in written[0]
  assert MASK in written[0]

# ---------------------------------------------------------------------------- #
# 4. routerLog returned system log lines untouched
# ---------------------------------------------------------------------------- #

async def test_theSystemLogIsRedactedBeforeItIsReturned():
  # services write credentials into the log, so it needs the same redaction as anything else
  noisy = "\n".join([
    "daemon.info adblock: download ok",
    "user.notice uci set wireless.wifinet1.key='hunter2'",
    "passwall.cfg1=subscribe_list",
    "passwall.cfg1.url='https://example.ru/sub/token123'",
  ])

  session = FakeSession(responses = {
    "logread -l 50": CommandResult(exitCode = 0, stdout = noisy, stderr = "")
  })

  result = await getSystemLog(session, makeSettings(), lines = 50)
  body = "\n".join(result["lines"])

  assert "hunter2" not in body
  assert "token123" not in body
  assert "download ok" in body

# ---------------------------------------------------------------------------- #
# the guards that were already there, kept honest
# ---------------------------------------------------------------------------- #

@pytest.mark.parametrize("payload", INJECTIONS)
async def test_pingTargetsStillCannotCarryACommand(payload):
  session = FakeSession()

  with pytest.raises(TargetError):
    await pingHost(session, makeSettings(), f"1.1.1.1{payload}")

  assert session.commands == []

async def test_aReadToolCannotBeUsedToWrite():
  # getSystemLog is READ, and READ must work with the gate shut
  session = FakeSession(responses = {
    "logread -l 5": CommandResult(exitCode = 0, stdout = "line", stderr = "")
  })

  result = await getSystemLog(session, makeSettings(writeEnabled = False), lines = 5)

  assert result["lines"] == ["line"]
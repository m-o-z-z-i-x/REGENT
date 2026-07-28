import pytest

from regent.system import factoryReset, upgradeFirmware, rebootDevice
from regent.packages import installPackage, removePackage, PackageError, PROTECTED_PACKAGES
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

# ---------------------------------------------------------------------------- #
# factory reset
# ---------------------------------------------------------------------------- #

async def test_factoryResetNeedsConfirmation():
  session = FakeSession()

  with pytest.raises(GuardError):
    await factoryReset(session, makeSettings(), confirm = False)

  assert session.commands == []

async def test_factoryResetNeedsTheGateEvenWhenConfirmed():
  session = FakeSession()

  with pytest.raises(GuardError):
    await factoryReset(session, makeSettings(writeEnabled = False), confirm = True)

  assert session.commands == []

async def test_factoryResetSaysTheSshKeyGoesToo():
  # the connection running this command is what disappears; saying so is the point
  session = FakeSession()

  result = await factoryReset(session, makeSettings(), confirm = True)

  assert "ssh key" in result["detail"]

async def test_factoryResetIsDetachedFromTheSession():
  session = FakeSession()

  await factoryReset(session, makeSettings(), confirm = True)

  assert any("nohup" in command and "firstboot" in command for command in session.commands)

# ---------------------------------------------------------------------------- #
# firmware
# ---------------------------------------------------------------------------- #

async def test_firmwareNeedsConfirmation():
  session = FakeSession()

  with pytest.raises(GuardError):
    await upgradeFirmware(session, makeSettings(), "/tmp/image.bin", confirm = False)

  assert session.commands == []

@pytest.mark.parametrize("hostile", ["/tmp/x.bin; reboot", "/tmp/$(reboot)", "../etc/passwd"])
async def test_firmwarePathsCannotCarryACommand(hostile):
  session = FakeSession()

  with pytest.raises(UnsafeValue):
    await upgradeFirmware(session, makeSettings(), hostile, confirm = True)

  assert session.commands == []

async def test_aMissingImageIsRefusedBeforeAnythingIsWritten():
  session = FakeSession(default = CommandResult(exitCode = 0, stdout = "no", stderr = ""))

  with pytest.raises(SystemError):
    await upgradeFirmware(session, makeSettings(), "/tmp/image.bin", confirm = True)

  assert not any("sysupgrade /tmp" in command for command in session.commands)

async def test_anImageThatFailsVerificationIsNeverWritten():
  # writing an image meant for another board bricks the router, so the check is never skipped
  session = FakeSession(responses = {
    "test -f /tmp/image.bin && echo yes || echo no": CommandResult(exitCode = 0, stdout = "yes", stderr = ""),
    "sysupgrade -T /tmp/image.bin 2>&1": CommandResult(exitCode = 1, stdout = "Image check failed", stderr = ""),
  })

  with pytest.raises(SystemError) as err:
    await upgradeFirmware(session, makeSettings(), "/tmp/image.bin", confirm = True)

  assert "not valid for this board" in str(err.value)
  assert not any(command.startswith("nohup") for command in session.commands)

async def test_aVerifiedImageIsFlashedDetached():
  session = FakeSession(responses = {
    "test -f /tmp/image.bin && echo yes || echo no": CommandResult(exitCode = 0, stdout = "yes", stderr = ""),
    "sysupgrade -T /tmp/image.bin 2>&1": CommandResult(exitCode = 0, stdout = "ok", stderr = ""),
    "wc -c < /tmp/image.bin": CommandResult(exitCode = 0, stdout = "8388608", stderr = ""),
  })

  result = await upgradeFirmware(session, makeSettings(), "/tmp/image.bin", confirm = True)

  assert result["rebooting"] is True
  assert "do not cut its power" in result["detail"]
  assert any("nohup" in command and "sysupgrade" in command for command in session.commands)

async def test_discardingSettingsIsPassedThrough():
  session = FakeSession(responses = {
    "test -f /tmp/image.bin && echo yes || echo no": CommandResult(exitCode = 0, stdout = "yes", stderr = ""),
    "sysupgrade -T /tmp/image.bin 2>&1": CommandResult(exitCode = 0, stdout = "ok", stderr = ""),
    "wc -c < /tmp/image.bin": CommandResult(exitCode = 0, stdout = "100", stderr = ""),
  })

  await upgradeFirmware(session, makeSettings(), "/tmp/image.bin", keepSettings = False, confirm = True)

  assert any("sysupgrade -n /tmp/image.bin" in command for command in session.commands)

# ---------------------------------------------------------------------------- #
# packages
# ---------------------------------------------------------------------------- #

@pytest.mark.parametrize("hostile", ["curl; reboot", "$(reboot)", "a b"])
async def test_packageNamesCannotCarryACommand(hostile):
  session = FakeSession()

  with pytest.raises(UnsafeValue):
    await installPackage(session, makeSettings(), hostile)

  assert session.commands == []

async def test_installIsRefusedWithTheGateShut():
  session = FakeSession()

  with pytest.raises(GuardError):
    await installPackage(session, makeSettings(writeEnabled = False), "tcpdump")

  assert session.commands == []

async def test_aNearlyFullOverlayStopsAnInstall():
  # a full root filesystem breaks services in ways that look unrelated to the install
  session = FakeSession(responses = {
    "command -v apk >/dev/null && echo apk || echo opkg": CommandResult(exitCode = 0, stdout = "opkg", stderr = ""),
    "df -k /overlay 2>/dev/null | tail -1 | awk '{print $4}'": CommandResult(exitCode = 0, stdout = "512", stderr = ""),
  })

  with pytest.raises(PackageError) as err:
    await installPackage(session, makeSettings(), "tcpdump")

  assert "free" in str(err.value)
  assert not any("opkg install" in command for command in session.commands)

async def test_apkIsUsedWhenThatIsWhatTheRouterHas():
  session = FakeSession(responses = {
    "command -v apk >/dev/null && echo apk || echo opkg": CommandResult(exitCode = 0, stdout = "apk", stderr = ""),
    "df -k /overlay 2>/dev/null | tail -1 | awk '{print $4}'": CommandResult(exitCode = 0, stdout = "9999999", stderr = ""),
  }, default = CommandResult(exitCode = 0, stdout = "yes", stderr = ""))

  result = await installPackage(session, makeSettings(), "tcpdump")

  assert result["manager"] == "apk"
  assert any("apk add tcpdump" in command for command in session.commands)

@pytest.mark.parametrize("name", sorted(PROTECTED_PACKAGES))
async def test_removingSomethingLoadBearingNeedsConfirmation(name):
  session = FakeSession()

  with pytest.raises(PackageError) as err:
    await removePackage(session, makeSettings(), name)

  assert "confirm=True" in str(err.value)
  assert session.commands == []

async def test_removingAnOrdinaryPackageJustWorks():
  session = FakeSession(responses = {
    "command -v apk >/dev/null && echo apk || echo opkg": CommandResult(exitCode = 0, stdout = "opkg", stderr = ""),
  }, default = CommandResult(exitCode = 0, stdout = "no", stderr = ""))

  result = await removePackage(session, makeSettings(), "tcpdump")

  assert result["installed"] is False
  assert any("opkg remove tcpdump" in command for command in session.commands)

# ---------------------------------------------------------------------------- #

async def test_rebootStillRefusesWithoutConfirmation():
  session = FakeSession()

  with pytest.raises(GuardError):
    await rebootDevice(session, makeSettings(), confirm = False)

  assert session.commands == []
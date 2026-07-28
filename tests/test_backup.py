import pytest

from regent.backup import (
  requireLabel, backupName, parseListing, parseChangedConfigs,
  listBackups, createBackup, inspectBackup, restoreBackup,
  BackupError, FALLBACK_DIRECTORY
)
from regent.guard import GuardError
from regent.metadata import SLUG
from regent.shell import UnsafeValue
from regent.ssh import CommandResult
from regent.config import Settings
from tests.fakes import FakeSession

REAL_DF = """Filesystem           Type            1K-blocks      Used Available Use% Mounted on
tmpfs                tmpfs               61016      6816     54200  11% /tmp
/dev/sda1            ext4             30089476    173236  28311552   1% /overlay"""

REAL_ARCHIVE = """etc/
etc/config/
etc/config/network
etc/config/wireless
etc/config/firewall
etc/config/dhcp
etc/dropbear/authorized_keys"""

def makeSettings(writeEnabled = True):
  return Settings(
    host = "192.168.10.1", user = "root", port = 22, keyPath = "./keys/key",
    timeout = 30, rollbackDelay = 90, writeEnabled = writeEnabled
  )

def test_requireLabelAcceptsPlainWords():
  assert requireLabel("before-vpn") == "before-vpn"

@pytest.mark.parametrize("hostile", ["a; reboot", "../etc", "a b", "a/b", ""])
def test_requireLabelRefusesAnythingThatReachesAFilename(hostile):
  with pytest.raises(BackupError):
    requireLabel(hostile)

def test_backupNameCarriesLabelAndStamp():
  # the project name comes from metadata, so renaming it does not strand this test
  assert backupName("before-vpn", "20260727-041500") == f"{SLUG}-before-vpn-20260727-041500.tar.gz"

def test_parseListingReadsSizeAndDate():
  output = f"{SLUG}-manual-20260727-0415.tar.gz 20480 Mon Jul 27 04:15:00 2026"
  backups = parseListing(output, f"/overlay/{SLUG}-backups")

  assert backups[0]["bytes"] == 20480
  assert backups[0]["path"] == f"/overlay/{SLUG}-backups/{SLUG}-manual-20260727-0415.tar.gz"

def test_parseListingSortsNewestFirst():
  output = "\n".join([
    f"{SLUG}-a-20260101-0000.tar.gz 100 x",
    f"{SLUG}-a-20260727-0000.tar.gz 100 x",
  ])

  assert parseListing(output, "/tmp")[0]["name"].endswith("20260727-0000.tar.gz")

def test_parseListingIgnoresNoise():
  assert parseListing("find: no such directory\n", "/tmp") == []

def test_parseChangedConfigsListsOnlyConfigFiles():
  configs = parseChangedConfigs(REAL_ARCHIVE)

  assert configs == ["dhcp", "firewall", "network", "wireless"]
  assert "authorized_keys" not in configs

async def test_backupsPreferStorageThatSurvivesAReboot():
  session = FakeSession(default = CommandResult(exitCode = 0, stdout = REAL_DF, stderr = ""))

  result = await listBackups(session, makeSettings())

  assert result["directory"].startswith("/overlay")
  assert result["onPersistentStorage"] is True

async def test_backupsFallBackToTmpAndSaySo():
  onlyRam = "Filesystem Type 1K-blocks Used Available Use% Mounted on\ntmpfs tmpfs 61016 6816 54200 11% /tmp"
  session = FakeSession(default = CommandResult(exitCode = 0, stdout = onlyRam, stderr = ""))

  result = await listBackups(session, makeSettings())

  assert result["directory"] == FALLBACK_DIRECTORY
  assert result["onPersistentStorage"] is False

async def test_creatingABackupIsRefusedWithTheGateShut():
  session = FakeSession()

  with pytest.raises(GuardError):
    await createBackup(session, makeSettings(writeEnabled = False))

  assert session.commands == []

async def test_creatingABackupWritesAnArchive():
  session = FakeSession(responses = {
    "df -Tk": CommandResult(exitCode = 0, stdout = REAL_DF, stderr = ""),
    "date +%Y%m%d-%H%M%S": CommandResult(exitCode = 0, stdout = "20260727-041500", stderr = ""),
    f"wc -c < /overlay/{SLUG}-backups/{SLUG}-before-vpn-20260727-041500.tar.gz 2>/dev/null":
      CommandResult(exitCode = 0, stdout = "20480", stderr = ""),
  })

  result = await createBackup(session, makeSettings(), label = "before-vpn")

  assert result["backup"]["bytes"] == 20480
  assert any("sysupgrade -b" in command for command in session.commands)

async def test_anEmptyArchiveIsTreatedAsAFailure():
  # sysupgrade can print an error and still exit zero; the file is the evidence
  session = FakeSession(responses = {
    "df -Tk": CommandResult(exitCode = 0, stdout = REAL_DF, stderr = ""),
    "date +%Y%m%d-%H%M%S": CommandResult(exitCode = 0, stdout = "20260727-041500", stderr = ""),
  }, default = CommandResult(exitCode = 0, stdout = "0", stderr = ""))

  with pytest.raises(BackupError):
    await createBackup(session, makeSettings(), label = "manual")

async def test_aBackupInTmpfsWarnsThatItDefeatsThePurpose():
  onlyRam = "Filesystem Type 1K-blocks Used Available Use% Mounted on\ntmpfs tmpfs 61016 6816 54200 11% /tmp"
  session = FakeSession(responses = {
    "df -Tk": CommandResult(exitCode = 0, stdout = onlyRam, stderr = ""),
    "date +%Y%m%d-%H%M%S": CommandResult(exitCode = 0, stdout = "20260727-041500", stderr = ""),
    f"wc -c < /tmp/{SLUG}-manual-20260727-041500.tar.gz 2>/dev/null":
      CommandResult(exitCode = 0, stdout = "20480", stderr = ""),
  })

  result = await createBackup(session, makeSettings())

  assert "will not survive a reboot" in result["detail"]

async def test_inspectListsTheConfigsInside():
  session = FakeSession(responses = {
    "tar -tzf /overlay/backup.tar.gz 2>/dev/null": CommandResult(exitCode = 0, stdout = REAL_ARCHIVE, stderr = "")
  })

  result = await inspectBackup(session, makeSettings(), "/overlay/backup.tar.gz")

  assert "network" in result["configs"]

@pytest.mark.parametrize("hostile", ["/tmp/x; reboot", "/tmp/$(reboot)", "../etc/passwd"])
async def test_pathsIntoBackupToolsCannotCarryACommand(hostile):
  session = FakeSession()

  with pytest.raises(UnsafeValue):
    await inspectBackup(session, makeSettings(), hostile)

  assert session.commands == []

async def test_restoreNeedsConfirmationEvenWithTheGateOpen():
  session = FakeSession()

  with pytest.raises(GuardError) as err:
    await restoreBackup(session, makeSettings(), "/overlay/backup.tar.gz", confirm = False)

  assert "confirm" in str(err.value).lower()
  assert session.commands == []

async def test_restoreRefusesAMissingFile():
  session = FakeSession(default = CommandResult(exitCode = 0, stdout = "no", stderr = ""))

  with pytest.raises(BackupError):
    await restoreBackup(session, makeSettings(), "/overlay/nope.tar.gz", confirm = True)

async def test_restoreRebootsDetachedSoItSurvivesTheSession():
  session = FakeSession(responses = {
    "test -f /overlay/backup.tar.gz && echo yes || echo no": CommandResult(exitCode = 0, stdout = "yes", stderr = ""),
    "tar -tzf /overlay/backup.tar.gz 2>/dev/null": CommandResult(exitCode = 0, stdout = REAL_ARCHIVE, stderr = ""),
  })

  result = await restoreBackup(session, makeSettings(), "/overlay/backup.tar.gz", confirm = True)

  assert result["rebooting"] is True
  assert "network" in result["changedConfigs"]
  assert any("nohup" in command and "reboot" in command for command in session.commands)
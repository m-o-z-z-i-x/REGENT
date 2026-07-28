import pytest

from regent.adblock import (
  parseStatus, parseSources, estimateWeight, memoryBudget, estimateDomainCeiling,
  resolveSources, getStatus, listSources, configureAdblock, waitForRun,
  readWorkingStorage, findPersistentStorage, moveWorkingStorage, parseMounts,
  parseDownloadFailures, clearStaleLock,
  AdblockError, PRESETS, SIZE_WEIGHTS, TMPBASE_OPTION, PID_FILE
)

# captured from the live Archer C59: the 32 GB stick already carries the overlay
REAL_DF = """Filesystem           Type            1K-blocks      Used Available Use% Mounted on
/dev/root            squashfs             2944      2944         0 100% /rom
tmpfs                tmpfs               61016      6816     54200  11% /tmp
/dev/sda1            ext4             30089476    173236  28311552   1% /overlay
overlayfs:/overlay   overlay          30089476    173236  28311552   1% /
tmpfs                tmpfs                 512         0       512   0% /dev"""
from regent.guard import GuardError
from regent.ssh import CommandResult
from regent.config import Settings
from tests.fakes import FakeSession

# captured from a real router whose adblock had been failing for months
REAL_STATUS = """::: adblock runtime information
  + adblock_status  : error
  + adblock_version : 4.2.3-3
  + blocked_domains : 0
  + active_sources  : adguard
  + dns_backend     : dnsmasq (-), /tmp/dnsmasq.d
  + run_utils       : download: /usr/bin/curl
  + last_run        : start, 1m 9s, 51 MB available, 1504 KB max. used, 2025-03-13T15:30:20+03:00
  + system          : TP-Link Archer C59 v1, ath79/generic, OpenWrt 23.05.4"""

REAL_SOURCES = """{
\t"adaway": {
\t\t"url": "https://adaway.org/hosts.txt",
\t\t"size": "S"
\t},
\t"oisd_big": {
\t\t"url": "https://big.oisd.nl/domainswild",
\t\t"size": "XXL"
\t},
\t"easylist": {
\t\t"url": "https://easylist.example/list.txt",
\t\t"size": "M"
\t}
}"""

def makeSettings(writeEnabled = True):
  return Settings(
    host = "192.168.10.1", user = "root", port = 22, keyPath = "./keys/key",
    timeout = 30, rollbackDelay = 90, writeEnabled = writeEnabled
  )

@pytest.fixture(autouse = True)
def fastPolling(monkeypatch):
  # the fake transport never changes its run stamp, so the real poll would wait the full timeout
  monkeypatch.setattr("regent.adblock.RUN_POLL_SECONDS", 0.001)
  monkeypatch.setattr("regent.adblock.RUN_MAX_WAIT_SECONDS", 0.01)

def memInfo(availableKb):
  return CommandResult(exitCode = 0, stdout = f"MemAvailable:    {availableKb} kB", stderr = "")

def test_parseStatusReadsTheFailedRun():
  parsed = parseStatus(REAL_STATUS)

  assert parsed["status"] == "error"
  assert parsed["blockedDomains"] == 0
  assert parsed["activeSources"] == ["adguard"]

def test_parseStatusKeepsTheLastRunStamp():
  assert "2025-03-13" in parseStatus(REAL_STATUS)["lastRun"]

def test_parseStatusHandlesThousandsSeparators():
  parsed = parseStatus("  + blocked_domains : 123 456")

  assert parsed["blockedDomains"] == 123456

def test_parseSourcesReadsNamesAndSizes():
  sources = parseSources(REAL_SOURCES)
  byName = {entry["name"]: entry["size"] for entry in sources}

  assert byName == {"adaway": "S", "oisd_big": "XXL", "easylist": "M"}

def test_estimateWeightAddsUpTheSizeTags():
  sizeByName = {"adaway": "S", "easylist": "M", "oisd_big": "XXL"}

  assert estimateWeight(["adaway", "easylist"], sizeByName) == SIZE_WEIGHTS["S"] + SIZE_WEIGHTS["M"]

def test_anXxlListDominatesTheBudget():
  sizeByName = {"adaway": "S", "oisd_big": "XXL"}

  assert estimateWeight(["oisd_big"], sizeByName) > estimateWeight(["adaway"] * 20, sizeByName)

def test_estimateWeightTreatsUnknownSizesAsMedium():
  assert estimateWeight(["mystery"], {}) == SIZE_WEIGHTS["M"]

def test_memoryBudgetGrowsWithFreeMemory():
  assert memoryBudget(64 * 1024 * 1024) > memoryBudget(16 * 1024 * 1024)

def test_workingOnDiskRaisesTheBudget():
  # in tmpfs the download and the sort use the same memory dnsmasq needs
  ram = 24 * 1024 * 1024

  assert memoryBudget(ram, workingOnDisk = True) > memoryBudget(ram, workingOnDisk = False)

def test_theDomainCeilingIsHigherWithTheScratchSpaceOnDisk():
  ram = 24 * 1024 * 1024

  assert estimateDomainCeiling(ram, workingOnDisk = True) > estimateDomainCeiling(ram, workingOnDisk = False)

def test_theCeilingMatchesWhatTheRouterActuallyDid():
  # the estimate must not fall far under what the same list really used
  ceiling = estimateDomainCeiling(16 * 1024 * 1024, workingOnDisk = False)

  assert 140_000 < ceiling < 200_000

def test_resolveSourcesExpandsAPreset():
  chosen, name = resolveSources("balanced", None)

  assert name == "balanced"
  assert "easyprivacy" in chosen

def test_explicitSourcesWinOverThePreset():
  chosen, name = resolveSources("balanced", ["adaway"])

  assert chosen == ["adaway"]
  assert name is None

def test_anUnknownPresetIsRefusedWithTheChoices():
  with pytest.raises(AdblockError) as err:
    resolveSources("everything", None)

  assert "balanced" in str(err.value)

def test_noPresetStacksTheXxlLists():
  # on a small router these take dnsmasq down, leaving the network with no DNS at all
  for names in PRESETS.values():
    assert "oisd_big" not in names
    assert "oisd_nsfw" not in names

async def test_statusReportsTheFailedRunFromTheRealRouter():
  session = FakeSession(responses = {
    "test -x /etc/init.d/adblock && echo yes || echo no": CommandResult(exitCode = 0, stdout = "yes", stderr = ""),
    "uci get adblock.global.adb_enabled 2>/dev/null": CommandResult(exitCode = 0, stdout = "1", stderr = ""),
    "/etc/init.d/adblock status 2>&1": CommandResult(exitCode = 0, stdout = REAL_STATUS, stderr = "")
  })

  status = await getStatus(session, makeSettings())

  assert status["installed"] is True
  assert status["enabled"] is True
  assert status["status"] == "error"
  assert status["blockedDomains"] == 0

async def test_statusOnARouterWithoutAdblock():
  session = FakeSession(responses = {
    "test -x /etc/init.d/adblock && echo yes || echo no": CommandResult(exitCode = 0, stdout = "no", stderr = "")
  })

  status = await getStatus(session, makeSettings())

  assert status["installed"] is False

async def test_workingStorageIsRecognisedAsRamWhenItIsTmpfs():
  session = FakeSession(responses = {
    f"uci get {TMPBASE_OPTION} 2>/dev/null": CommandResult(exitCode = 0, stdout = "", stderr = ""),
    "df -T /tmp 2>/dev/null | tail -1 | awk '{print $2}'": CommandResult(exitCode = 0, stdout = "tmpfs", stderr = ""),
    "df -k /tmp 2>/dev/null | tail -1 | awk '{print $4}'": CommandResult(exitCode = 0, stdout = "54169", stderr = "")
  })

  storage = await readWorkingStorage(session)

  assert storage["path"] == "/tmp"
  assert storage["onDisk"] is False

async def test_workingStorageOnExt4CountsAsDisk():
  session = FakeSession(responses = {
    f"uci get {TMPBASE_OPTION} 2>/dev/null": CommandResult(exitCode = 0, stdout = "/overlay/adblock", stderr = ""),
    "df -T /overlay/adblock 2>/dev/null | tail -1 | awk '{print $2}'": CommandResult(exitCode = 0, stdout = "ext4", stderr = ""),
    "df -k /overlay/adblock 2>/dev/null | tail -1 | awk '{print $4}'": CommandResult(exitCode = 0, stdout = "28311552", stderr = "")
  })

  storage = await readWorkingStorage(session)

  assert storage["onDisk"] is True
  assert storage["freeBytes"] > 20 * 1024 ** 3

async def test_findPersistentStorageIgnoresRamBackedMounts():
  session = FakeSession(default = CommandResult(exitCode = 0, stdout = REAL_DF, stderr = ""))

  candidates = await findPersistentStorage(session)

  assert [entry["mount"] for entry in candidates] == ["/overlay"]

def test_parseMountsKeepsOnlyRealStorageWithRoom():
  # captured from the live router, where the usb stick already carries the overlay
  candidates = parseMounts(REAL_DF)

  assert len(candidates) == 1
  assert candidates[0]["mount"] == "/overlay"
  assert candidates[0]["filesystem"] == "ext4"
  assert candidates[0]["freeBytes"] > 25 * 1024 ** 3

def test_parseMountsRejectsTmpfsHoweverLarge():
  # tmpfs is RAM wearing a filesystem costume; moving work there saves nothing
  huge = "Filesystem Type 1K-blocks Used Available Use% Mounted on\ntmpfs tmpfs 8388608 0 8388608 0% /tmp"

  assert parseMounts(huge) == []

def test_parseMountsRejectsReadOnlyRoots():
  rom = "Filesystem Type 1K-blocks Used Available Use% Mounted on\n/dev/root squashfs 4096 4096 999999999 100% /rom"

  assert parseMounts(rom) == []

def test_parseMountsDoesNotOfferTheSameStickTwice():
  # "/" is a view of /overlay, so listing both would offer the same disk twice
  mounts = [entry["mount"] for entry in parseMounts(REAL_DF)]

  assert "/" not in mounts

def test_parseMountsSkipsAnythingTooSmallToMatter():
  small = "Filesystem Type 1K-blocks Used Available Use% Mounted on\n/dev/sdb1 ext4 20000 100 10000 1% /mnt/tiny"

  assert parseMounts(small) == []

async def test_moveWorkingStorageRefusesAReadOnlyTarget():
  session = FakeSession(default = CommandResult(exitCode = 1, stdout = "", stderr = "Read-only file system"))

  with pytest.raises(AdblockError) as err:
    await moveWorkingStorage(session, makeSettings(), "/mnt/stick")

  assert "read-only" in str(err.value).lower()
  assert not any(command.startswith("uci commit") for command in session.commands)

async def test_moveWorkingStorageIsRefusedWhenTheWriteGateIsClosed():
  session = FakeSession()

  with pytest.raises(GuardError):
    await moveWorkingStorage(session, makeSettings(writeEnabled = False), "/overlay")

  assert session.commands == []

async def test_configureIsRefusedWhenTheWriteGateIsClosed():
  session = FakeSession()

  with pytest.raises(GuardError):
    await configureAdblock(session, makeSettings(writeEnabled = False))

  assert session.commands == []

async def test_configureRefusesASetThatWouldNotFitInMemory():
  # the point of the check: an out-of-memory dnsmasq leaves the network with no DNS
  session = FakeSession(responses = {
    f"zcat /etc/adblock/adblock.sources.gz 2>/dev/null": CommandResult(exitCode = 0, stdout = REAL_SOURCES, stderr = ""),
    "grep MemAvailable /proc/meminfo": memInfo(2048)
  })

  with pytest.raises(AdblockError) as err:
    await configureAdblock(session, makeSettings(), sources = ["oisd_big"])

  assert "OOM" in str(err.value) or "memory" in str(err.value)
  assert not any("uci set adblock" in command for command in session.commands)

async def test_forceOverridesTheMemoryCheckButSaysSo():
  session = FakeSession(responses = {
    f"zcat /etc/adblock/adblock.sources.gz 2>/dev/null": CommandResult(exitCode = 0, stdout = REAL_SOURCES, stderr = ""),
    "grep MemAvailable /proc/meminfo": memInfo(2048),
    "test -x /etc/init.d/adblock && echo yes || echo no": CommandResult(exitCode = 0, stdout = "yes", stderr = ""),
    "uci get adblock.global.adb_enabled 2>/dev/null": CommandResult(exitCode = 0, stdout = "1", stderr = ""),
    "/etc/init.d/adblock status 2>&1": CommandResult(exitCode = 0, stdout = REAL_STATUS, stderr = "")
  })

  result = await configureAdblock(session, makeSettings(), sources = ["oisd_big"], force = True)

  assert result["applied"] is True
  assert any("force" in warning for warning in result["warnings"])

async def test_configureRefusesListsThisBuildDoesNotHave():
  session = FakeSession(responses = {
    f"zcat /etc/adblock/adblock.sources.gz 2>/dev/null": CommandResult(exitCode = 0, stdout = REAL_SOURCES, stderr = "")
  })

  with pytest.raises(AdblockError) as err:
    await configureAdblock(session, makeSettings(), sources = ["not_a_list"])

  assert "not_a_list" in str(err.value)

class SequencedSession(FakeSession):
  # answers the status command differently on each call, so a wait can be observed
  def __init__(self, statuses, **kwargs):
    super().__init__(**kwargs)
    self.statuses = list(statuses)

  async def run(self, command):
    if command == "/etc/init.d/adblock status 2>&1" and self.statuses:
      self.commands.append(command)
      entry = self.statuses.pop(0)
      status, lastRun = entry if isinstance(entry, tuple) else (entry, "run-1")

      return CommandResult(
        exitCode = 0,
        stdout = f"  + adblock_status  : {status}\n  + last_run        : {lastRun}",
        stderr = ""
      )

    return await super().run(command)

async def test_waitForRunKeepsPollingWhileTheRebuildIsRunning():
  # reload returns as soon as the job starts, so the status right after it is still the old one
  session = SequencedSession(["running", "running", "enabled"])

  assert await waitForRun(session, maxWaitSeconds = 1, pollSeconds = 0.001) == "enabled"
  assert session.commands.count("/etc/init.d/adblock status 2>&1") == 3

async def test_waitForRunGivesUpRatherThanHangingForever():
  session = SequencedSession(["running"] * 200)

  assert await waitForRun(session, maxWaitSeconds = 0.05, pollSeconds = 0.01) == "running"

async def test_waitForRunIgnoresTheStaleStatusLeftByThePreviousRun():
  # just after reload the status still describes the previous run and looks finished
  session = SequencedSession([
    ("enabled", "run-1"),   # stale: the job has not taken over yet
    ("running", "run-1"),
    ("enabled", "run-2"),   # the real outcome
  ])

  status = await waitForRun(session, previousRun = "run-1", maxWaitSeconds = 1, pollSeconds = 0.001)

  assert status == "enabled"
  assert session.commands.count("/etc/init.d/adblock status 2>&1") == 3

async def test_waitForRunAcceptsAnyFinishedRunWhenNoPreviousStampIsGiven():
  session = SequencedSession([("enabled", "run-1")])

  assert await waitForRun(session, maxWaitSeconds = 1, pollSeconds = 0.001) == "enabled"

async def test_waitForRunStillPollsOnceWhenThePollIntervalIsZero():
  # a zero interval must still end the loop, rather than running until the fake data runs out
  session = SequencedSession(["running"] * 10)

  assert await waitForRun(session, maxWaitSeconds = 5, pollSeconds = 0) == "running"
  assert session.commands.count("/etc/init.d/adblock status 2>&1") == 1

# captured from a real router: three lists time out and one url is gone
REAL_FAILURE_LOG = """Mon Jul 27 02:01:41 2026 user.info adblock-4.2.3-3[18276]: download of 'easyprivacy' failed, url: https://easylist-downloads.adblockplus.org/easyprivacy.txt, rule: BEGIN{FS=x}, categories: -, rc: 28, log: curl: (28) SSL connection timeout
Mon Jul 27 02:01:49 2026 user.info adblock-4.2.3-3[18276]: download of 'games_tracking' failed, url: https://raw.githubusercontent.com/KodoPengin/GameIndustry-hosts-Template/master/Main-Template/hosts, rule: /^0/, categories: -, rc: 56, log: curl: (56) The requested URL returned error: 404
Mon Jul 27 02:02:10 2026 user.info adblock-4.2.3-3[18276]: download of 'reg_ru' failed, url: https://easylist-downloads.adblockplus.org/ruadlist.txt, rule: BEGIN{FS=y}, categories: -, rc: 28, log: curl: (28) SSL connection timeout
Mon Jul 27 02:03:02 2026 user.info adblock-4.2.3-3[18276]: blocklist with overall 166088 blocked domains loaded successfully"""

async def test_clearStaleLockRemovesTheLockOfADeadRun():
  # a pid file from a killed run makes adblock report success while doing nothing
  session = FakeSession(responses = {
    f"cat {PID_FILE} 2>/dev/null": CommandResult(exitCode = 0, stdout = "18276", stderr = ""),
    "test -d /proc/18276 && echo yes || echo no": CommandResult(exitCode = 0, stdout = "no", stderr = ""),
    "uci get adblock.global.adb_rtfile 2>/dev/null": CommandResult(exitCode = 0, stdout = "", stderr = "")
  })

  result = await clearStaleLock(session)

  assert result == {"cleared": True, "pid": 18276}
  assert f"rm -f {PID_FILE}" in session.commands
  assert "rm -f /tmp/adb_runtime.json" in session.commands

async def test_clearStaleLockLeavesALiveRunAlone():
  session = FakeSession(responses = {
    f"cat {PID_FILE} 2>/dev/null": CommandResult(exitCode = 0, stdout = "4242", stderr = ""),
    "test -d /proc/4242 && echo yes || echo no": CommandResult(exitCode = 0, stdout = "yes", stderr = "")
  })

  result = await clearStaleLock(session)

  assert result == {"cleared": False, "pid": 4242}
  assert not any(command.startswith("rm -f") for command in session.commands)

async def test_clearStaleLockDoesNothingWithoutALockFile():
  session = FakeSession(responses = {
    f"cat {PID_FILE} 2>/dev/null": CommandResult(exitCode = 0, stdout = "", stderr = "")
  })

  assert await clearStaleLock(session) == {"cleared": False, "pid": None}

def test_parseDownloadFailuresNamesEachDeadList():
  failures = parseDownloadFailures(REAL_FAILURE_LOG)

  assert sorted(failures) == ["easyprivacy", "games_tracking", "reg_ru"]

def test_parseDownloadFailuresKeepsTheReason():
  failures = parseDownloadFailures(REAL_FAILURE_LOG)

  assert "SSL connection timeout" in failures["easyprivacy"]
  assert "404" in failures["games_tracking"]

def test_parseDownloadFailuresIgnoresASuccessfulRun():
  assert parseDownloadFailures("blocklist with overall 166088 blocked domains loaded successfully") == {}

async def test_configureReportsListsThatDidNotDownload():
  # a list that will not fetch is dead weight: it costs a wait and blocks nothing
  session = FakeSession(responses = {
    "zcat /etc/adblock/adblock.sources.gz 2>/dev/null": CommandResult(exitCode = 0, stdout = REAL_SOURCES, stderr = ""),
    "grep MemAvailable /proc/meminfo": memInfo(65536),
    "test -x /etc/init.d/adblock && echo yes || echo no": CommandResult(exitCode = 0, stdout = "yes", stderr = ""),
    "uci get adblock.global.adb_enabled 2>/dev/null": CommandResult(exitCode = 0, stdout = "1", stderr = ""),
    "/etc/init.d/adblock status 2>&1": CommandResult(exitCode = 0, stdout = REAL_STATUS, stderr = ""),
    "logread -e adblock | tail -60": CommandResult(exitCode = 0, stdout = REAL_FAILURE_LOG, stderr = "")
  })

  result = await configureAdblock(session, makeSettings(), sources = ["adaway", "easylist"])

  # only lists actually chosen are reported, not every failure in the log
  assert result["failedSources"] == []

async def test_configureNamesTheChosenListsThatFailed():
  session = FakeSession(responses = {
    "zcat /etc/adblock/adblock.sources.gz 2>/dev/null": CommandResult(
      exitCode = 0,
      stdout = REAL_SOURCES.replace('"easylist"', '"reg_ru"'),
      stderr = ""
    ),
    "grep MemAvailable /proc/meminfo": memInfo(65536),
    "test -x /etc/init.d/adblock && echo yes || echo no": CommandResult(exitCode = 0, stdout = "yes", stderr = ""),
    "uci get adblock.global.adb_enabled 2>/dev/null": CommandResult(exitCode = 0, stdout = "1", stderr = ""),
    "/etc/init.d/adblock status 2>&1": CommandResult(exitCode = 0, stdout = REAL_STATUS, stderr = ""),
    "logread -e adblock | tail -60": CommandResult(exitCode = 0, stdout = REAL_FAILURE_LOG, stderr = "")
  })

  result = await configureAdblock(session, makeSettings(), sources = ["adaway", "reg_ru"])

  assert result["failedSources"] == ["reg_ru"]
  assert any("did not download" in warning for warning in result["warnings"])

async def test_configureReloadsBecauseCommittingAloneChangesNothing():
  session = FakeSession(responses = {
    f"zcat /etc/adblock/adblock.sources.gz 2>/dev/null": CommandResult(exitCode = 0, stdout = REAL_SOURCES, stderr = ""),
    "grep MemAvailable /proc/meminfo": memInfo(65536),
    "test -x /etc/init.d/adblock && echo yes || echo no": CommandResult(exitCode = 0, stdout = "yes", stderr = ""),
    "uci get adblock.global.adb_enabled 2>/dev/null": CommandResult(exitCode = 0, stdout = "1", stderr = ""),
    "/etc/init.d/adblock status 2>&1": CommandResult(exitCode = 0, stdout = REAL_STATUS, stderr = "")
  })

  await configureAdblock(session, makeSettings(), sources = ["adaway", "easylist"])

  assert "uci commit adblock" in session.commands
  assert "/etc/init.d/adblock reload" in session.commands
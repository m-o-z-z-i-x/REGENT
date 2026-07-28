import pytest

from regent.vpn import (
  parseVersion, sameGeneration, probeCommand, readHandshake,
  collectNodes, findGlobalSection, detectPackageManager, detectVariant,
  isPackageInstalled, getVpnStatus, probeNode, VpnError,
  NFT_DEPENDENCIES, DNS_CONFLICT_OPTIONS
)
from regent.ssh import CommandResult
from regent.config import Settings
from regent.uci import parseShow
from tests.fakes import FakeSession

# shaped like the live router's passwall2 config
REAL_CONFIG = """passwall2.cfg023fd6=global
passwall2.cfg023fd6.enabled='1'
passwall2.cfg023fd6.node='AUvCtqYu'
passwall2.cfg023fd6.dns_redirect='0'
passwall2.cfg023fd6.remote_fakedns='0'
passwall2.AUvCtqYu=nodes
passwall2.AUvCtqYu.remarks='Poland'
passwall2.AUvCtqYu.protocol='vless'
passwall2.AUvCtqYu.address='203.0.113.10'
passwall2.AUvCtqYu.port='80'"""

def makeSettings(writeEnabled = True):
  return Settings(
    host = "192.168.10.1", user = "root", port = 22, keyPath = "./keys/key",
    timeout = 30, rollbackDelay = 90, writeEnabled = writeEnabled
  )

def test_parseVersionReadsTheUsualShape():
  assert parseVersion("luci-app-passwall2 - 26.7.16") == (26, 7, 16)

def test_parseVersionReadsTheCoreBanner():
  assert parseVersion("Xray 25.3.6 (Xray, Penetrates Everything.)") == (25, 3, 6)

def test_parseVersionReturnsNothingForGarbage():
  assert parseVersion("no numbers here") is None

def test_matchingGenerationsPass():
  assert sameGeneration((26, 7, 16), (26, 7, 11)) is True

def test_aNewerAppAgainstAnOlderCoreIsCaught():
  # a newer app on an older core writes a shape the core cannot read
  assert sameGeneration((26, 7, 16), (25, 3, 6)) is False

def test_anUnknownVersionIsNotTreatedAsAMismatch():
  # refusing to act because a version could not be read would be worse than the risk
  assert sameGeneration(None, (25, 3, 6)) is True

def test_theProbeDoesNotUseNetcat():
  # busybox nc reports every port closed, including ones that answer
  command = probeCommand("203.0.113.10", "80")

  assert "nc " not in command
  assert "time_connect" in command

def test_readHandshakeTreatsANumberAsSuccess():
  assert readHandshake("0.044") == 0.044

def test_readHandshakeTreatsEmptyOutputAsFailure():
  assert readHandshake("") == 0.0
  assert readHandshake(None) == 0.0

def test_collectNodesFindsTheSelectedOne():
  nodes = collectNodes(parseShow(REAL_CONFIG), "AUvCtqYu")

  assert len(nodes) == 1
  assert nodes[0]["remarks"] == "Poland"
  assert nodes[0]["address"] == "203.0.113.10"
  assert nodes[0]["selected"] is True

def test_findGlobalSectionLocatesTheConfigRoot():
  assert findGlobalSection(parseShow(REAL_CONFIG)) == "cfg023fd6"

async def test_apkIsDetectedRatherThanAssumed():
  # openwrt 24.10 replaced opkg with apk; assuming opkg would break the whole domain
  session = FakeSession(responses = {
    "command -v apk >/dev/null && echo apk || echo opkg": CommandResult(exitCode = 0, stdout = "apk", stderr = "")
  })

  assert await detectPackageManager(session) == "apk"

async def test_opkgIsTheFallback():
  session = FakeSession(responses = {
    "command -v apk >/dev/null && echo apk || echo opkg": CommandResult(exitCode = 0, stdout = "opkg", stderr = "")
  })

  assert await detectPackageManager(session) == "opkg"

async def test_packageLookupUsesTheRightToolForApk():
  session = FakeSession(default = CommandResult(exitCode = 0, stdout = "yes", stderr = ""))

  await isPackageInstalled(session, "apk", "kmod-nft-tproxy")

  assert any("apk info" in command for command in session.commands)
  assert not any("opkg" in command for command in session.commands)

async def test_bothPasswallGenerationsAreLookedFor():
  session = FakeSession(responses = {
    "test -x /etc/init.d/passwall2 && echo yes || echo no": CommandResult(exitCode = 0, stdout = "no", stderr = ""),
    "test -x /etc/init.d/passwall && echo yes || echo no": CommandResult(exitCode = 0, stdout = "yes", stderr = "")
  })

  assert await detectVariant(session) == "passwall"

async def test_aRouterWithoutPasswallReportsSoQuietly():
  session = FakeSession(default = CommandResult(exitCode = 0, stdout = "no", stderr = ""))

  status = await getVpnStatus(session, makeSettings())

  assert status["installed"] is False
  assert status["problems"] == []

async def test_aMissingTproxyModuleIsReportedAsTheSilentFailureItIs():
  # without it passwall starts, logs a warning and proxies nothing at all
  session = FakeSession(responses = {
    "test -x /etc/init.d/passwall2 && echo yes || echo no": CommandResult(exitCode = 0, stdout = "yes", stderr = ""),
    "command -v apk >/dev/null && echo apk || echo opkg": CommandResult(exitCode = 0, stdout = "opkg", stderr = ""),
    "uci show passwall2": CommandResult(exitCode = 0, stdout = REAL_CONFIG, stderr = ""),
    "command -v nft >/dev/null && echo yes || echo no": CommandResult(exitCode = 0, stdout = "yes", stderr = ""),
    "xray version 2>/dev/null | head -1": CommandResult(exitCode = 0, stdout = "Xray 26.7.11", stderr = ""),
    "opkg list-installed 2>/dev/null | grep '^luci-app-passwall2 '": CommandResult(exitCode = 0, stdout = "luci-app-passwall2 - 26.7.16", stderr = ""),
  }, default = CommandResult(exitCode = 0, stdout = "no", stderr = ""))

  status = await getVpnStatus(session, makeSettings())
  kinds = [problem["kind"] for problem in status["problems"]]

  assert "missing-dependency" in kinds
  assert any("kmod-nft-tproxy" in problem["detail"] for problem in status["problems"])

async def test_dnsRedirectIsFlaggedBecauseItDefeatsAdblock():
  conflicted = REAL_CONFIG.replace("dns_redirect='0'", "dns_redirect='1'")
  session = FakeSession(responses = {
    "test -x /etc/init.d/passwall2 && echo yes || echo no": CommandResult(exitCode = 0, stdout = "yes", stderr = ""),
    "command -v apk >/dev/null && echo apk || echo opkg": CommandResult(exitCode = 0, stdout = "opkg", stderr = ""),
    "uci show passwall2": CommandResult(exitCode = 0, stdout = conflicted, stderr = ""),
    "command -v nft >/dev/null && echo yes || echo no": CommandResult(exitCode = 0, stdout = "yes", stderr = ""),
    "xray version 2>/dev/null | head -1": CommandResult(exitCode = 0, stdout = "Xray 26.7.11", stderr = ""),
    "uci get passwall2.cfg023fd6.dns_redirect 2>/dev/null": CommandResult(exitCode = 0, stdout = "1", stderr = ""),
  }, default = CommandResult(exitCode = 0, stdout = "yes", stderr = ""))

  status = await getVpnStatus(session, makeSettings())

  assert any(problem["kind"] == "dns-conflict" for problem in status["problems"])

async def test_probeNodeReportsAHandshake():
  session = FakeSession(responses = {
    "test -x /etc/init.d/passwall2 && echo yes || echo no": CommandResult(exitCode = 0, stdout = "yes", stderr = ""),
    "uci show passwall2": CommandResult(exitCode = 0, stdout = REAL_CONFIG, stderr = ""),
    probeCommand("203.0.113.10", "80"): CommandResult(exitCode = 0, stdout = "0.044", stderr = "")
  })

  result = await probeNode(session, makeSettings())

  assert result["reachable"] is True
  assert result["handshakeMs"] == 44.0
  assert result["remarks"] == "Poland"

async def test_probeNodeReportsADeadNode():
  session = FakeSession(responses = {
    "test -x /etc/init.d/passwall2 && echo yes || echo no": CommandResult(exitCode = 0, stdout = "yes", stderr = ""),
    "uci show passwall2": CommandResult(exitCode = 0, stdout = REAL_CONFIG, stderr = ""),
    probeCommand("203.0.113.10", "80"): CommandResult(exitCode = 0, stdout = "", stderr = "")
  })

  result = await probeNode(session, makeSettings())

  assert result["reachable"] is False
  assert result["handshakeMs"] is None

async def test_probeNodeRefusesWithoutASelection():
  bare = "passwall2.cfg023fd6=global\npasswall2.cfg023fd6.enabled='1'"
  session = FakeSession(responses = {
    "test -x /etc/init.d/passwall2 && echo yes || echo no": CommandResult(exitCode = 0, stdout = "yes", stderr = ""),
    "uci show passwall2": CommandResult(exitCode = 0, stdout = bare, stderr = "")
  })

  with pytest.raises(VpnError):
    await probeNode(session, makeSettings())
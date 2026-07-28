import pytest

from regent.diagnostics import (
  parsePing, parseResolve, requireSafeTarget, TargetError,
  getSystemLog, pingHost, resolveHostname, LOG_LIMIT
)

from regent.ssh import CommandResult
from regent.config import Settings
from tests.fakes import FakeSession

# captured verbatim from the live Archer C59
REAL_PING = """PING 1.1.1.1 (1.1.1.1): 56 data bytes
64 bytes from 1.1.1.1: seq=0 ttl=58 time=29.658 ms
64 bytes from 1.1.1.1: seq=1 ttl=58 time=30.379 ms

--- 1.1.1.1 ping statistics ---
2 packets transmitted, 2 packets received, 0% packet loss
round-trip min/avg/max = 29.658/30.018/30.379 ms"""

REAL_NSLOOKUP = """Server:\t\t127.0.0.1
Address:\t127.0.0.1:53

Non-authoritative answer:
Name:\topenwrt.org
Address: 64.226.122.113

Non-authoritative answer:
Name:\topenwrt.org
Address: 2a03:b0c0:3:d0::1a51:c001"""

def makeSettings():
  return Settings(
    host = "192.168.10.1", user = "root", port = 22, keyPath = "./keys/key",
    timeout = 30, rollbackDelay = 90, writeEnabled = False
  )

def test_requireSafeTargetAcceptsPlainHosts():
  assert requireSafeTarget("openwrt.org") == "openwrt.org"
  assert requireSafeTarget("1.1.1.1") == "1.1.1.1"

@pytest.mark.parametrize("hostile", [
  "example.com; reboot",
  "$(reboot)",
  "a`reboot`",
  "a && rm -rf /",
  "a|cat /etc/shadow",
  "",
])

def test_requireSafeTargetRefusesShellMetacharacters(hostile):
  # these strings reach a shell command line, so a hole here is remote code execution
  with pytest.raises(TargetError):
    requireSafeTarget(hostile)

def test_parsePingReadsTheRealStatistics():
  parsed = parsePing(REAL_PING)

  assert parsed["transmitted"] == 2
  assert parsed["received"] == 2
  assert parsed["lossPercent"] == 0
  assert parsed["avgMs"] == 30.018

def test_parsePingHandlesTotalLoss():
  parsed = parsePing("--- 1.1.1.1 ping statistics ---\n3 packets transmitted, 0 packets received, 100% packet loss")

  assert parsed["received"] == 0
  assert parsed["lossPercent"] == 100
  assert parsed["avgMs"] is None

def test_parsePingSurvivesGarbage():
  assert parsePing("host unreachable")["lossPercent"] == 100

def test_parseResolveSkipsTheServersOwnAddress():
  # busybox prints the resolver's own address before the answer, and it is not a result
  addresses = parseResolve(REAL_NSLOOKUP)

  assert "127.0.0.1:53" not in addresses
  assert "64.226.122.113" in addresses

def test_parseResolveKeepsBothFamilies():
  addresses = parseResolve(REAL_NSLOOKUP)

  assert "2a03:b0c0:3:d0::1a51:c001" in addresses

def test_parseResolveReturnsEmptyOnFailure():
  assert parseResolve("** server can't find nope.invalid: NXDOMAIN") == []

async def test_getSystemLogClampsToTheLimit():
  session = FakeSession(responses = {
    f"logread -l {LOG_LIMIT}": CommandResult(exitCode = 0, stdout = "line one\nline two", stderr = "")
  })

  result = await getSystemLog(session, makeSettings(), lines = 99999)

  assert result["requested"] == LOG_LIMIT
  assert result["lines"] == ["line one", "line two"]

async def test_pingHostReportsReachability():
  session = FakeSession(responses = {
    "ping -c 2 -W 2 1.1.1.1 2>&1": CommandResult(exitCode = 0, stdout = REAL_PING, stderr = "")
  })

  result = await pingHost(session, makeSettings(), "1.1.1.1", count = 2)

  assert result["reachable"] is True
  assert result["avgMs"] == 30.018

async def test_pingHostRefusesAnInjectedTarget():
  session = FakeSession()

  with pytest.raises(TargetError):
    await pingHost(session, makeSettings(), "1.1.1.1; reboot")

  assert session.commands == []

async def test_resolveHostnameReturnsAddresses():
  session = FakeSession(responses = {
    "nslookup openwrt.org 2>&1": CommandResult(exitCode = 0, stdout = REAL_NSLOOKUP, stderr = "")
  })

  result = await resolveHostname(session, makeSettings(), "openwrt.org")

  assert result["resolved"] is True
  assert "64.226.122.113" in result["addresses"]
import pytest

from regent.packages import parsePackageList, quoteTerm, getInstalledPackages, findPackage, SEARCH_LIMIT
from regent.ssh import CommandResult
from regent.config import Settings
from tests.fakes import FakeSession

# captured verbatim from the live Archer C59
REAL_INSTALLED = """adblock - 4.2.3-3
ath10k-board-qca9888 - 20230804-1
ath10k-firmware-qca9888-ct - 2020-11-08-1
base-files - 1559-r24012-d8dd03c46f"""

def makeSettings():
  return Settings(
    host = "192.168.10.1", user = "root", port = 22, keyPath = "./keys/key",
    timeout = 30, rollbackDelay = 90, writeEnabled = False
  )

def test_parsePackageListReadsTheRealFormat():
  packages = parsePackageList(REAL_INSTALLED)

  assert len(packages) == 4
  assert packages[0] == {"name": "adblock", "version": "4.2.3-3", "description": None}

def test_parsePackageListKeepsVersionsWithHyphens():
  assert parsePackageList("base-files - 1559-r24012-d8dd03c46f")[0]["version"] == "1559-r24012-d8dd03c46f"

def test_parsePackageListCapturesDescriptions():
  package = parsePackageList("wget - 1.21 - retrieves files over HTTP")[0]

  assert package["name"] == "wget"
  assert package["version"] == "1.21"
  assert package["description"] == "retrieves files over HTTP"

def test_parsePackageListSkipsJunkLines():
  assert parsePackageList("\n\nnonsense\n") == []

def test_quoteTermEscapesQuotesSoTheGrepCannotBreakOut():
  assert quoteTerm("it's") == "it'\\''s"

async def test_getInstalledPackagesCountsThem():
  session = FakeSession(responses = {
    "opkg list-installed": CommandResult(exitCode = 0, stdout = REAL_INSTALLED, stderr = "")
  })

  result = await getInstalledPackages(session, makeSettings())

  assert result["count"] == 4
  assert result["packages"][0]["name"] == "adblock"

async def test_findPackageFlagsWhatIsAlreadyInstalled():
  session = FakeSession(responses = {
    f"opkg list | grep -i 'adblock' | head -{SEARCH_LIMIT}": CommandResult(
      exitCode = 0, stdout = "adblock - 4.2.3-3\nluci-app-adblock - 4.2.0", stderr = ""
    ),
    "opkg list-installed | grep -i 'adblock'": CommandResult(
      exitCode = 0, stdout = "adblock - 4.2.3-3", stderr = ""
    )
  })

  result = await findPackage(session, makeSettings(), "adblock")
  byName = {package["name"]: package for package in result["packages"]}

  assert byName["adblock"]["installed"] is True
  assert byName["luci-app-adblock"]["installed"] is False

async def test_findPackageReportsTruncationAtTheLimit():
  many = "\n".join(f"pkg{index} - 1.0" for index in range(SEARCH_LIMIT))
  session = FakeSession(responses = {
    f"opkg list | grep -i 'a' | head -{SEARCH_LIMIT}": CommandResult(exitCode = 0, stdout = many, stderr = ""),
    "opkg list-installed | grep -i 'a'": CommandResult(exitCode = 0, stdout = "", stderr = "")
  })

  result = await findPackage(session, makeSettings(), "a")

  assert result["truncated"] is True
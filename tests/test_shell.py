import pytest

from regent.shell import (
  requireSafeName, requireSafePath, requireSafeHost, quoteArgument,
  findMetacharacter, UnsafeValue
)

# every one of these was reachable from a tool parameter before this module existed
INJECTIONS = [
  "lan; reboot",
  "lan && rm -rf /",
  "lan | tee /etc/passwd",
  "$(reboot)",
  "`reboot`",
  "lan\nreboot",
  "lan > /etc/shadow",
  "lan'; uci delete network.lan; '",
]

@pytest.mark.parametrize("hostile", INJECTIONS)
def test_namesRefuseEveryInjection(hostile):
  with pytest.raises(UnsafeValue):
    requireSafeName(hostile)

@pytest.mark.parametrize("hostile", INJECTIONS)
def test_pathsRefuseEveryInjection(hostile):
  with pytest.raises(UnsafeValue):
    requireSafePath(f"/mnt/{hostile}")

@pytest.mark.parametrize("hostile", INJECTIONS)
def test_hostsRefuseEveryInjection(hostile):
  with pytest.raises(UnsafeValue):
    requireSafeHost(hostile)

@pytest.mark.parametrize("ordinary", ["lan", "wwan", "lan6", "guest_net", "cfg023fd6"])
def test_ordinaryNamesPass(ordinary):
  assert requireSafeName(ordinary) == ordinary

@pytest.mark.parametrize("section", ["@zone[0]", "@forwarding[12]", "@wifi-iface[1]"])
def test_uciAnonymousSectionsPass(section):
  # uci's own way of addressing an anonymous section, so it has to be accepted
  assert requireSafeName(section) == section

def test_bracketsMayOnlyHoldDigits():
  # they are glob characters, so anything else between them is a shell hazard
  with pytest.raises(UnsafeValue):
    requireSafeName("@zone[a-z]")

@pytest.mark.parametrize("ordinary", ["/tmp", "/overlay", "/mnt/usb-stick", "/etc/config"])
def test_ordinaryPathsPass(ordinary):
  assert requireSafePath(ordinary) == ordinary

def test_relativePathsAreRefused():
  # a relative path means something different depending on the working directory
  with pytest.raises(UnsafeValue):
    requireSafePath("tmp/adblock")

def test_walkingUpwardsIsRefused():
  with pytest.raises(UnsafeValue) as err:
    requireSafePath("/mnt/../../etc")

  assert ".." in str(err.value)

def test_emptyValuesAreRefused():
  for check in (requireSafeName, requireSafePath, requireSafeHost):
    with pytest.raises(UnsafeValue):
      check("")

def test_theRefusalNamesWhatItFound():
  # "invalid" tells the model nothing it can act on
  with pytest.raises(UnsafeValue) as err:
    requireSafeName("lan; reboot")

  assert ";" in str(err.value)

def test_findMetacharacterReturnsNothingForCleanText():
  assert findMetacharacter("lan") is None

def test_quoteArgumentWrapsPlainValues():
  assert quoteArgument("dhcp") == "'dhcp'"

def test_quoteArgumentSurvivesAnEmbeddedQuote():
  assert quoteArgument("it's") == "'it'\\''s'"

def test_quoteArgumentNeutralisesAnInjectionAttempt():
  quoted = quoteArgument("x'; reboot; '")

  # the payload is still present as text, but no longer as syntax
  assert quoted.startswith("'") and quoted.endswith("'")
  assert "'\\''" in quoted

def test_aVeryLongNameIsRefused():
  with pytest.raises(UnsafeValue):
    requireSafeName("a" * 200)
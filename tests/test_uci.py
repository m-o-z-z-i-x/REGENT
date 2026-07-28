import pytest

from regent.uci import (
  buildShow, buildGet, buildSet, buildDelete, buildCommit, buildRevert,
  parseShow, quoteValue, UciError
)

def test_quoteValueWrapsPlainStrings():
  assert quoteValue("dhcp") == "'dhcp'"

def test_quoteValueEscapesEmbeddedQuotes():
  assert quoteValue("it's") == "'it'\\''s'"

def test_buildShowTargetsOneConfig():
  assert buildShow("network") == "uci show network"

def test_buildGetUsesTheFullPath():
  assert buildGet("network.lan.proto") == "uci get network.lan.proto"

def test_buildSetQuotesTheValue():
  assert buildSet("network.wan.proto", "dhcp") == "uci set network.wan.proto='dhcp'"

def test_buildDeleteTargetsThePath():
  assert buildDelete("firewall.@zone[1]") == "uci delete firewall.@zone[1]"

def test_buildCommitWithoutConfigCommitsEverything():
  assert buildCommit() == "uci commit"

def test_buildCommitWithConfigIsScoped():
  assert buildCommit("network") == "uci commit network"

def test_buildRevertWithConfigIsScoped():
  assert buildRevert("firewall") == "uci revert firewall"

def test_buildSetRejectsAPathWithoutASection():
  with pytest.raises(UciError):
    buildSet("network", "dhcp")

def test_parseShowBuildsNestedDictionaries():
  output = "\n".join([
    "network.lan=interface",
    "network.lan.proto='static'",
    "network.lan.ipaddr='192.168.1.1'",
    "network.wan=interface",
    "network.wan.proto='dhcp'"
  ])

  parsed = parseShow(output)

  assert parsed["lan"][".type"] == "interface"
  assert parsed["lan"]["proto"] == "static"
  assert parsed["lan"]["ipaddr"] == "192.168.1.1"
  assert parsed["wan"]["proto"] == "dhcp"

def test_parseShowKeepsListValuesAsLists():
  output = "\n".join([
    "firewall.@zone[0]=zone",
    "firewall.@zone[0].network='lan' 'lan6'"
  ])

  parsed = parseShow(output)

  assert parsed["@zone[0]"]["network"] == ["lan", "lan6"]

def test_parseShowIgnoresBlankLines():
  assert parseShow("\n\n  \n") == {}
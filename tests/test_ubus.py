import pytest

from regent.ubus import buildCall, buildList, parseReply, UbusError

def test_buildListEnumeratesObjects():
  assert buildList() == "ubus list"

def test_buildCallWithoutParamsSendsEmptyObject():
  assert buildCall("system", "board") == "ubus call system board"

def test_buildCallSerialisesParamsAsJson():
  command = buildCall("uci", "get", {"config": "network"})

  assert command == "ubus call uci get '{\"config\": \"network\"}'"

def test_parseReplyReadsJson():
  assert parseReply('{"uptime": 4242}') == {"uptime": 4242}

def test_parseReplyTreatsEmptyOutputAsEmptyDict():
  assert parseReply("") == {}

def test_parseReplyRejectsGarbage():
  with pytest.raises(UbusError) as err:
    parseReply("Command failed: Not found")

  assert "Not found" in str(err.value)
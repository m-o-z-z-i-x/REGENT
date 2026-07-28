import pytest

from fastmcp.exceptions import ToolError

from regent.errors import toolSafe
from regent.ssh import SshError
from regent.uci import UciError
from regent.ubus import UbusError
from regent.guard import GuardError
from regent.metadata import envName

async def test_toolSafePassesThroughSuccessfulResults():
  @toolSafe
  async def works():
    return {"ok": True}

  assert await works() == {"ok": True}

async def test_toolSafeTranslatesGuardError():
  @toolSafe
  async def refused():
    raise GuardError(f"the write gate is closed - set {envName('ENABLE_WRITE')}=1")

  with pytest.raises(ToolError) as err:
    await refused()

  assert envName("ENABLE_WRITE") in str(err.value)

async def test_toolSafeTranslatesSshError():
  @toolSafe
  async def unreachable():
    raise SshError("cannot reach 192.168.1.1:22")

  with pytest.raises(ToolError) as err:
    await unreachable()

  assert "192.168.1.1" in str(err.value)

async def test_toolSafeTranslatesParsingErrors():
  @toolSafe
  async def badReply():
    raise UbusError("ubus did not return json: Command failed")

  with pytest.raises(ToolError) as err:
    await badReply()

  assert "json" in str(err.value)

async def test_toolSafeTranslatesUciError():
  @toolSafe
  async def badPath():
    raise UciError("'network' is a config name, not a section path")

  with pytest.raises(ToolError) as err:
    await badPath()

  assert "section path" in str(err.value)

async def test_toolSafeHidesUnexpectedFailuresBehindAGenericMessage():
  @toolSafe
  async def explodes():
    raise ZeroDivisionError("division by zero")

  with pytest.raises(ToolError) as err:
    await explodes()

  assert "division by zero" not in str(err.value)
  assert "logs/" in str(err.value)

async def test_toolSafeKeepsTheWrappedFunctionName():
  @toolSafe
  async def namedTool():
    return None

  assert namedTool.__name__ == "namedTool"
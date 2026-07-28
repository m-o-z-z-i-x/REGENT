import pytest

import regent.ssh

from regent.ssh import CommandResult, SshError, RouterSession
from regent.config import Settings
from tests.fakes import FakeSession

def makeSettings():
  return Settings(
    host = "192.168.1.1",
    user = "root",
    port = 22,
    keyPath = "./keys/key",
    timeout = 30,
    rollbackDelay = 90,
    writeEnabled = False
  )

def test_commandResultOkIsTrueOnZeroExit():
  assert CommandResult(exitCode = 0, stdout = "", stderr = "").ok is True

def test_commandResultOkIsFalseOnNonZeroExit():
  assert CommandResult(exitCode = 1, stdout = "", stderr = "nope").ok is False

def test_routerSessionRefusesMissingKey():
  settings = makeSettings()
  settings.keyPath = "./keys/does-not-exist"

  session = RouterSession(settings)

  with pytest.raises(SshError) as err:
    session.resolveKeyPath()

  assert "does-not-exist" in str(err.value)

async def test_connectDisablesGssapi(monkeypatch, tmp_path):
  # removing this argument breaks connecting on some windows machines
  keyFile = tmp_path / "key"
  keyFile.write_text("not a real key")

  captured = {}

  async def fakeConnect(host, **kwargs):
    captured["host"] = host
    captured.update(kwargs)

    return object()

  monkeypatch.setattr(regent.ssh.asyncssh, "connect", fakeConnect)

  settings = makeSettings()
  settings.keyPath = str(keyFile)

  await RouterSession(settings).connect()

  assert "gss_host" in captured, "gss_host must be passed explicitly, not left to asyncssh's default"
  assert captured["gss_host"] is None

async def test_fakeSessionRecordsCommands():
  session = FakeSession(responses = {
    "uci show network": CommandResult(exitCode = 0, stdout = "network.lan=interface", stderr = "")
  })

  await session.connect()
  result = await session.run("uci show network")

  assert result.stdout == "network.lan=interface"
  assert session.commands == ["uci show network"]

async def test_fakeSessionRaisesOnFailOn():
  session = FakeSession(failOn = "reboot")

  with pytest.raises(SshError):
    await session.run("reboot")

async def test_aLocalBlockIsNotReportedAsAnUnreachableRouter(monkeypatch, tmp_path):
  # this error means the local machine refused the socket, so the router was never contacted
  keyFile = tmp_path / "key"
  keyFile.write_text("not a real key")

  async def refuse(host, **kwargs):
    raise PermissionError(13, "forbidden by its access permissions")

  monkeypatch.setattr(regent.ssh.asyncssh, "connect", refuse)

  settings = Settings(
    host = "192.0.2.1", user = "root", port = 22, keyPath = str(keyFile),
    timeout = 30, rollbackDelay = 90, writeEnabled = False
  )

  with pytest.raises(SshError) as err:
    await RouterSession(settings).connect()

  message = str(err.value)

  assert "this machine blocked" in message
  assert "cannot reach" not in message
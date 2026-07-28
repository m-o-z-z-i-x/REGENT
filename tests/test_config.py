import pytest

import regent.config

from regent.config import loadSettings, ConfigError
from regent.metadata import envName

@pytest.fixture(autouse = True)
def ignoreDotenv(monkeypatch):
  # these cover validation and defaults, so a developer's own .env must not change the result
  monkeypatch.setattr(regent.config, "load_dotenv", lambda *args, **kwargs: False)

def test_loadSettingsReadsEnvironment(monkeypatch):
  # built through envName, so renaming the prefix moves the tests with the code
  monkeypatch.setenv(envName("HOST"), "10.0.0.1")
  monkeypatch.setenv(envName("USER"), "admin")
  monkeypatch.setenv(envName("PORT"), "2222")
  monkeypatch.setenv(envName("KEY"), "./keys/key")
  monkeypatch.setenv(envName("ENABLE_WRITE"), "1")

  settings = loadSettings()

  assert settings.host == "10.0.0.1"
  assert settings.user == "admin"
  assert settings.port == 2222
  assert settings.writeEnabled is True

def test_loadSettingsAppliesDefaults(monkeypatch):
  monkeypatch.setenv(envName("HOST"), "192.168.1.1")
  monkeypatch.delenv(envName("USER"), raising = False)
  monkeypatch.delenv(envName("PORT"), raising = False)
  monkeypatch.delenv(envName("ENABLE_WRITE"), raising = False)

  settings = loadSettings()

  assert settings.user == "root"
  assert settings.port == 22
  assert settings.timeout == 30
  assert settings.rollbackDelay == 90
  assert settings.writeEnabled is False

def test_loadSettingsRejectsMissingHost(monkeypatch):
  monkeypatch.delenv(envName("HOST"), raising = False)

  with pytest.raises(ConfigError) as err:
    loadSettings()

  assert envName("HOST") in str(err.value)

def test_theEnvPrefixIsNotTheProjectName():
  # renaming the project must not invalidate a .env somebody already wrote
  from regent.metadata import APP_NAME

  assert APP_NAME.lower() not in envName("HOST").lower()

def test_theExampleEnvDoesNotPinTheKeyToAProjectPath(monkeypatch):
  # a relative OPENWRT_KEY here would override the resolver and send an installed server
  # looking for the key under whatever directory it started in
  import pathlib

  example = pathlib.Path(".env.example").read_text(encoding = "utf-8")
  active = [
    line for line in example.split("\n")
    if line.strip().startswith("OPENWRT_KEY=")
  ]

  assert active == [], f"OPENWRT_KEY is set in .env.example: {active}"

def test_theMissingHostMessageDoesNotPointAtAFileTheUserLacks(monkeypatch):
  # .env.example ships only in the sdist, so the message has to carry the settings itself
  monkeypatch.delenv(envName("HOST"), raising = False)

  with pytest.raises(ConfigError) as err:
    loadSettings()

  message = str(err.value)

  assert ".env.example" not in message
  assert envName("USER") in message
  assert envName("PORT") in message
  assert ".env" in message
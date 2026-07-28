# ---------------------------------------------------------------------------- #
# DESCRIPTION: the README makes a checkable claim, so it is checked
# ---------------------------------------------------------------------------- #

# DEPENDENCIES --------------------------------------------------------------- #
import pathlib
import re

import pytest
# ---------------------------------------------------------------------------- #

# LOGIC ---------------------------------------------------------------------- #
def test_theClaimedTestCountIsTheRealOne(pytestconfig):
  # the suite knows its own size, so it is the thing that should complain
  onDisk = {path.name for path in pathlib.Path("tests").glob("test_*.py")}

  if not onDisk <= getattr(pytestconfig, "collectedFiles", set()):
    pytest.skip("only part of the suite was collected, so its size proves nothing")

  readme = pathlib.Path("README.md").read_text(encoding = "utf-8")
  claimed = int(re.search(r"all (\d+) tests", readme).group(1))

  assert claimed == pytestconfig.collectedCount, (
    f"README claims {claimed} tests, the suite collected {pytestconfig.collectedCount}"
  )
# ---------------------------------------------------------------------------- #
def test_everySettingTheCodeReadsIsDocumented():
  # a setting the README never names is unusable, since nobody can guess its spelling
  config = pathlib.Path("regent/config.py").read_text(encoding = "utf-8")
  readme = pathlib.Path("README.md").read_text(encoding = "utf-8")

  from regent.metadata import envName

  read = {envName(name) for name in re.findall(r"envName\(\"(\w+)\"\)", config)}
  absent = sorted(name for name in read if name not in readme)

  assert absent == [], f"the server reads these but the README never names them: {absent}"
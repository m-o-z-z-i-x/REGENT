# ---------------------------------------------------------------------------- #
# DESCRIPTION: records what the collection actually gathered, for the README check
# ---------------------------------------------------------------------------- #

# DEPENDENCIES --------------------------------------------------------------- #
import pathlib
# ---------------------------------------------------------------------------- #

# LOGIC ---------------------------------------------------------------------- #
def pytest_collection_modifyitems(session, config, items):
  # the count must include parametrised cases, which only collection knows. the file set
  # lets the README check skip itself when only part of the suite was run
  config.collectedCount = len(items)
  config.collectedFiles = {pathlib.Path(item.location[0]).name for item in items}
# ---------------------------------------------------------------------------- #
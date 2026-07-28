# ---------------------------------------------------------------------------- #
# DESCRIPTION: translate internal failures into actionable mcp tool errors
# ---------------------------------------------------------------------------- #

# DEPENDENCIES --------------------------------------------------------------- #
from functools import wraps

from fastmcp.exceptions import ToolError

from regent.ssh import SshError
from regent.uci import UciError
from regent.ubus import UbusError
from regent.guard import GuardError
from regent.logger import exception
# ---------------------------------------------------------------------------- #

# LOGIC ---------------------------------------------------------------------- #
EXPECTED_FAILURES = (GuardError, SshError, UciError, UbusError)

GENERIC_MESSAGE = "the server hit an unexpected error - see logs/execution-report.log for the details"

def toolSafe(fn):
  # expected failures already read well and pass through. anything else is a bug, and its traceback is noise
  @wraps(fn)
  async def wrapper(*args, **kwargs):
    try:
      return await fn(*args, **kwargs)
    except EXPECTED_FAILURES as err:
      raise ToolError(str(err)) from err
    except Exception as err:
      exception(err)

      raise ToolError(GENERIC_MESSAGE) from err

  return wrapper
# ---------------------------------------------------------------------------- #
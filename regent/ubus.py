# ---------------------------------------------------------------------------- #
# DESCRIPTION: pure builders and parsers for the ubus rpc interface
# ---------------------------------------------------------------------------- #

# DEPENDENCIES --------------------------------------------------------------- #
from json import dumps, loads, JSONDecodeError
# ---------------------------------------------------------------------------- #

# LOGIC ---------------------------------------------------------------------- #
class UbusError(Exception):
  pass

def buildList():
  return "ubus list"

def buildCall(objectName, method, params = None):
  # ubus takes the payload as one quoted json argument and rejects an empty string
  if not params:
    return f"ubus call {objectName} {method}"

  payload = dumps(params).replace("'", "'\\''")

  return f"ubus call {objectName} {method} '{payload}'"

def parseReply(output):
  # ubus prints nothing when there is no data, and a plain error line when the call is wrong
  text = output.strip()

  if not text:
    return {}

  try:
    return loads(text)
  except JSONDecodeError as err:
    raise UbusError(f"ubus did not return json: {text}") from err
# ---------------------------------------------------------------------------- #
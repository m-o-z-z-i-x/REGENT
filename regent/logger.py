# ---------------------------------------------------------------------------- #
# DESCRIPTION: file logger for execution reports and router command audit
# ---------------------------------------------------------------------------- #

# DEPENDENCIES --------------------------------------------------------------- #
import logging

from os import path, makedirs
from traceback import extract_tb

from regent.metadata import SLUG
from regent.paths import logPath
# ---------------------------------------------------------------------------- #

# LOGIC ---------------------------------------------------------------------- #
LOG_FILE_PATH = logPath()
LOG_DIR = path.dirname(LOG_FILE_PATH)

def ensureDir(directory):
  # create the directory and its parents, reporting success instead of raising
  if not directory:
    return True

  try:
    makedirs(directory, exist_ok = True)

    return True
  except Exception:
    return False

class LineFeedFileHandler(logging.FileHandler):
  # the stock handler translates newlines on windows, which would leave the log with CRLF endings
  def _open(self):
    return open(self.baseFilename, self.mode, encoding = self.encoding, newline = "\n")

def _buildHandler():
  # fall back to a null handler so a read-only location does not stop the server starting
  if ensureDir(LOG_DIR):
    try:
      handler = LineFeedFileHandler(LOG_FILE_PATH, mode = "a", encoding = "utf-8")

      # one space after the level. padding would make the gap depend on the level's length
      handler.setFormatter(logging.Formatter(
        "[%(asctime)s] %(levelname)s -- %(message)s",
        datefmt = "%Y-%m-%d %H:%M:%S"
      ))

      return handler
    except Exception:
      pass

  return logging.NullHandler()

_logger = logging.getLogger(SLUG)

_logger.setLevel(logging.DEBUG)

_logger.propagate = False

if not _logger.handlers:
  _logger.addHandler(_buildHandler())

def exception(err):
  tb = extract_tb(err.__traceback__) if err.__traceback__ else []

  if tb:
    filename, line, func, text = tb[-1]
    filename = path.basename(filename)

    _logger.error(f"[FILE] {filename} -- [LINE] {line}: {err}")
  else:
    _logger.error(str(err))

def warning(msg):
  _logger.warning(msg)

def audit(command, exitCode):
  # the record of every command that reached the router
  #
  # imported here rather than at module scope, which would create a circular import
  from regent.secrets import redactCommand

  _logger.info(f"[ROUTER] exit={exitCode} -- {redactCommand(command)}")
# ---------------------------------------------------------------------------- #
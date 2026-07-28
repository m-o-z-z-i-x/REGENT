import logging
import re

from regent.logger import ensureDir, audit, LineFeedFileHandler, _buildHandler, LOG_FILE_PATH

def makeRecord(level, message = "a message"):
  return logging.LogRecord("test", level, "test.py", 1, message, None, None)

def test_ensureDirCreatesDirectory(tmp_path):
  target = tmp_path / "nested" / "deep"

  assert ensureDir(str(target)) is True
  assert target.exists()

def test_ensureDirAcceptsEmptyPath():
  assert ensureDir("") is True

def test_auditWritesTheCommandAndExitCode():
  audit("uci show network", 0)

  with open(LOG_FILE_PATH, "r", encoding = "utf-8") as handle:
    contents = handle.read()

  assert "uci show network" in contents
  assert "exit=0" in contents

# ---------------------------------------------------------------------------- #
# these ask the formatter directly, since the log file keeps lines written by older code
# ---------------------------------------------------------------------------- #

def test_everyLevelIsFollowedByExactlyOneSpace():
  # padding would make the gap depend on the level's length, which reads as uneven
  formatter = _buildHandler().formatter
  levels = (logging.DEBUG, logging.INFO, logging.WARNING, logging.ERROR, logging.CRITICAL)

  gaps = {
    len(match.group(1))
    for level in levels
    if (match := re.search(r"\] \w+( +)--", formatter.format(makeRecord(level))))
  }

  assert gaps == {1}, f"levels are followed by {sorted(gaps)} space(s), expected only 1"

def test_theLevelAndMessageBothSurviveTheFormat():
  line = _buildHandler().formatter.format(makeRecord(logging.WARNING, "gate closed"))

  assert "WARNING -- gate closed" in line

def test_theHandlerWritesLineFeedsNotCarriageReturns(tmp_path):
  # the stock handler translates newlines on windows, which would leave the log with CRLF
  target = tmp_path / "sample.log"
  handler = LineFeedFileHandler(str(target), mode = "a", encoding = "utf-8")

  handler.emit(makeRecord(logging.INFO, "first"))
  handler.emit(makeRecord(logging.INFO, "second"))
  handler.close()

  assert b"\r\n" not in target.read_bytes()
  assert target.read_bytes().count(b"\n") == 2
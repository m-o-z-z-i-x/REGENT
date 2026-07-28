import pytest

from regent.guard import READ, WRITE, DESTRUCTIVE, GuardError, checkTier, annotationsFor, TIER_HINTS
from regent.config import Settings
from regent.metadata import envName

def makeSettings(writeEnabled):
  return Settings(
    host = "192.168.1.1",
    user = "root",
    port = 22,
    keyPath = "./keys/key",
    timeout = 30,
    rollbackDelay = 90,
    writeEnabled = writeEnabled
  )

def test_readIsAlwaysAllowed():
  checkTier(READ, makeSettings(writeEnabled = False))

def test_writeIsRefusedWhenTheGateIsClosed():
  with pytest.raises(GuardError) as err:
    checkTier(WRITE, makeSettings(writeEnabled = False))

  assert envName("ENABLE_WRITE") in str(err.value)

def test_writeIsAllowedWhenTheGateIsOpen():
  checkTier(WRITE, makeSettings(writeEnabled = True))

def test_destructiveNeedsTheGateEvenWhenConfirmed():
  with pytest.raises(GuardError) as err:
    checkTier(DESTRUCTIVE, makeSettings(writeEnabled = False), confirmed = True)

  assert envName("ENABLE_WRITE") in str(err.value)

def test_destructiveNeedsConfirmationEvenWhenTheGateIsOpen():
  with pytest.raises(GuardError) as err:
    checkTier(DESTRUCTIVE, makeSettings(writeEnabled = True), confirmed = False)

  assert "confirm" in str(err.value).lower()

def test_destructiveIsAllowedWithBothGateAndConfirmation():
  checkTier(DESTRUCTIVE, makeSettings(writeEnabled = True), confirmed = True)

def test_unknownTierIsRefused():
  with pytest.raises(GuardError):
    checkTier("whatever", makeSettings(writeEnabled = True))

def test_annotationsCarryTheTitle():
  assert annotationsFor(READ, "System log")["title"] == "System log"

def test_readAdvertisesItselfAsReadOnly():
  hints = annotationsFor(READ, "x")

  assert hints["readOnlyHint"] is True
  assert hints["destructiveHint"] is False
  assert hints["idempotentHint"] is True

def test_writeDoesNotClaimToBeReadOnly():
  hints = annotationsFor(WRITE, "x")

  assert hints["readOnlyHint"] is False
  assert hints["destructiveHint"] is False

def test_destructiveAdvertisesItself():
  # clients use this hint to decide whether to ask the user first
  hints = annotationsFor(DESTRUCTIVE, "Reboot")

  assert hints["destructiveHint"] is True
  assert hints["readOnlyHint"] is False

def test_everyTierTalksToTheOutsideWorld():
  # each tool reaches a router this server neither owns nor can predict
  assert all(hints["openWorldHint"] is True for hints in TIER_HINTS.values())

def test_onlyReadIsAdvertisedAsReadOnly():
  # the hints and the gate must agree, or a client would auto-approve something gated
  readOnly = [tier for tier, hints in TIER_HINTS.items() if hints["readOnlyHint"]]

  assert readOnly == [READ]

def test_annotationsRejectAnUnknownTier():
  with pytest.raises(GuardError):
    annotationsFor("whatever", "x")